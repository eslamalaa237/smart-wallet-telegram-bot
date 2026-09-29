import re
import uuid
import asyncio
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import (
    get_now,
    check_rate_limit,
    is_authorized,
    get_user_identifier,
    normalize_wallet,
)
from keyboards import get_main_keyboard, get_nav_keyboard
from sheets_db import (
    fetch_all_transactions,
    add_to_sheet,
    add_transactions,
    update_tx_amount,
    update_tx_wallet,
    delete_from_sheet,
    add_debt,
    pay_debt,
    fetch_debts,
    delete_debt,
    add_subscription,
    fetch_subscriptions,
    delete_subscription,
    get_category_budgets,
    set_category_budget,
    delete_category_budget,
    fetch_installments,
    add_installment,
    pay_installment,
    delete_installment,
    register_active_chat,
    get_all_active_users,
    get_recurring_income,
    set_recurring_income,
    get_monthly_budget,
    set_monthly_budget,
    get_savings_goal,
    set_savings_goal,
    log_user_action,
    pop_last_user_action,
    peek_last_user_action,
    async_upload_receipt_image,
    normalize_ar_search,
    clear_sheets_cache,
    get_user_categories,
    add_user_category,
    remove_user_category,
    get_user_pin,
    set_user_pin,
)


from reports import (
    calculate_wallet_balance,
    format_wallet_message,
    format_savings_status,
    format_budget_status,
    format_debts_msg,
    format_subs_msg,
    format_installments_msg,
    generate_monthly_closing_report,
    check_budget_alert,
    generate_daily_report,
    generate_income_report,
    generate_report_by_criteria,
    generate_yearly_report,
    generate_monthly_comparison,
    send_excel_to_user,
    send_chart_to_user,
)
from ai_engine import (
    analyze_user_request,
    analyze_receipt_image,
    get_financial_advice,
    fast_parse_deletion,
    find_matching_transaction,
)


async def edit_or_send(query, text: str, reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception:
        if query.message:
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


# ==========================================
# 1. التنبيهات المجدولة (Scheduled Jobs)
# ==========================================
async def daily_evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    users = get_all_active_users()
    for user_key, chat_id in users.items():
        try:
            msg = (
                "🌙 **مساء الخير! تذكيرك المالي اليومي:**\n"
                "هل قمت بأي مصاريف أو دخل اليوم لم تسجله بعد؟ 📝\n"
                "اكتبه الآن برسالة أو فويس سريع قبل نهاية اليوم!"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("☀️ ملخص مصاريف اليوم", callback_data="period:today:all")],
                [InlineKeyboardButton("✨ كله تمام متسجل", callback_data="action:cancel")]
            ])
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            continue


async def daily_subscription_check(context: ContextTypes.DEFAULT_TYPE):
    today_day = get_now().day
    users = get_all_active_users()
    for user_key, chat_id in users.items():
        try:
            subs = await fetch_subscriptions(user_key)
            due_subs = [s for s in subs if s["due_day"] == today_day]
            for s in due_subs:
                msg = (
                    f"🔔 **تذكير استحقاق اشتراك اليوم ({today_day}):**\n\n"
                    f"📌 **الاشتراك:** {s['name']}\n"
                    f"💰 **المبلغ المستحق:** `{s['amount']:,.2f}` ج.م\n"
                    f"هل قمت بسداده اليوم؟"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💵 دفع كاش", callback_data=f"paysub:نقدي:{s['name']}:{s['amount']}")],
                    [InlineKeyboardButton("💳 دفع بالفيزا", callback_data=f"paysub:فيزا:{s['name']}:{s['amount']}")],
                    [InlineKeyboardButton("⏳ تأجيل", callback_data="action:cancel")]
                ])
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=kb)

            # تذكير الأقساط المستحقة اليوم
            insts = fetch_installments(user_key)
            due_insts = [i for i in insts if i.get("status") == "نشط" and i.get("due_day") == today_day]
            for inst in due_insts:
                amt = float(inst.get("monthly_amount", 0))
                p_m = int(inst.get("paid_months", 0)) + 1
                t_m = int(inst.get("total_months", 1))
                msg = (
                    f"💳 **تذكير استحقاق قسط اليوم ({today_day}):**\n\n"
                    f"📦 **القسط:** {inst['name']}\n"
                    f"💰 **المبلغ المستحق:** `{amt:,.2f}` ج.م [قسط {p_m} من {t_m}]\n"
                    f"اضغط أدناه للسداد الفوري وتسجيل المصروف:"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"💳 سداد القسط ({amt:,.0f}ج)", callback_data=f"payinst:{inst['id']}")],
                    [InlineKeyboardButton("⏳ تأجيل", callback_data="action:cancel")]
                ])
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            continue


async def monthly_closing_job(context: ContextTypes.DEFAULT_TYPE):
    """جوب يرسل التقرير الختامي للشهر المنقضي تلقائياً كل أول شهر في تمام الساعة 9 صباحاً."""
    now = get_now()
    if now.day != 1:
        return
    users = get_all_active_users()
    for user_key, chat_id in users.items():
        try:
            transactions = await fetch_all_transactions(user_key)
            report = generate_monthly_closing_report(user_key, transactions)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 تفاصيل المصاريف", callback_data="period:this_month:all")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="action:balance")]
            ])
            await context.bot.send_message(chat_id=chat_id, text=report, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            continue


async def auto_recurring_income_job(context: ContextTypes.DEFAULT_TYPE):
    """جوب يومي لتسجيل الدخل الشهري المتكرر (المرتب) تلقائياً."""
    today_day = get_now().day
    users = get_all_active_users()
    for user_key, chat_id in users.items():
        try:
            ri = get_recurring_income(user_key)
            if ri and ri.get("amount", 0) > 0 and ri.get("day", 0) == today_day:
                date_str = get_now().strftime("%Y-%m-%d")
                wallet = normalize_wallet(ri.get("wallet", "فيزا"))
                desc = ri.get("description", "مرتب شهري")
                await add_to_sheet(user_key, desc, ri["amount"], "دخل", "مرتب", date_str, wallet)
                transactions = await fetch_all_transactions(user_key)
                balances = calculate_wallet_balance(transactions)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💰 **تم تسجيل دخلك الشهري تلقائياً!**\n\n"
                        f"📝 **{desc}**: `{ri['amount']:,.2f}` ج.م ({wallet})\n"
                        f"💼 **رصيدك الإجمالي الآن:** `{balances['total']:,.2f}` ج.م"
                    ),
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard(),
                )
        except Exception:
            continue


async def weekly_debt_reminder(context: ContextTypes.DEFAULT_TYPE):
    """تذكير أسبوعي بالديون النشطة."""
    users = get_all_active_users()
    for user_key, chat_id in users.items():
        try:
            debts = await fetch_debts(user_key)
            active_debts = [d for d in debts if d["status"] == "نشط" and d["remaining"] > 0]
            if not active_debts:
                continue

            total_on_me = sum(d["remaining"] for d in active_debts if d["type"] == "عليا")
            total_for_me = sum(d["remaining"] for d in active_debts if d["type"] == "ليا")

            if total_on_me > 0 or total_for_me > 0:
                msg = "🔔 **تذكير أسبوعي بالديون:**\n\n"
                if total_on_me > 0:
                    msg += f"🔴 **عليك ديون بإجمالي:** `{total_on_me:,.0f}` ج.م\n"
                if total_for_me > 0:
                    msg += f"🟢 **لك ديون عند آخرين بإجمالي:** `{total_for_me:,.0f}` ج.م\n"
                msg += "\nاكتب /debts لعرض التفاصيل الكاملة."

                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 عرض الديون", callback_data="action:debts")],
                    [InlineKeyboardButton("✨ تمام", callback_data="action:cancel")]
                ])
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            continue


# ==========================================
# 2. معالجات الأوامر (Command Handlers)
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.effective_message.reply_text("⛔ عذراً، هذا البوت مخصص للمصرح لهم فقط.")
        return

    user_key = get_user_identifier(user)
    register_active_chat(user_key, update.effective_chat.id)
    status = await update.effective_message.reply_text(f"جاري تحضير محفظتك ({user_key}) من Google Sheets... ⏳")

    transactions = await fetch_all_transactions(user_key)
    balances = calculate_wallet_balance(transactions)

    msg = (
        f"أهلاً بك يا `{user_key}` في محفظتك المالية الذكية ⚡📊\n\n"
        + format_wallet_message(user_key, balances)
        + "\n\n💡 **ميزة التسجيل الفوري:** اكتب ما صرفته أو استلفته أو حولته وسيتسجل تلقائياً!"
    )
    await status.edit_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def execute_undo_last_action(user_key: str) -> str:
    """تنفيذ التراجع الشامل عن آخر إجراء تم من المستخدم أياً كان نوعه (مصروف، دخل، ديون، اشتراكات، ميزانية، أهداف، تحويلات)."""
    last_act = pop_last_user_action(user_key)
    if last_act:
        act_type = last_act.get("action_type")
        summary = last_act.get("summary", "آخر إجراء")

        if act_type == "transaction":
            tx_ids = last_act.get("tx_ids", [])
            await delete_from_sheet(user_key, tx_ids)
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            return (
                f"🗑️ **تم إلغاء وحذف المعاملة بنجاح!**\n\n"
                f"📝 **الإجراء الملغي:** {summary}\n"
                f"🔖 كود المعاملة: `{', '.join(tx_ids)}`\n"
                "─────────────────────\n"
                f"💼 **محفظتك بعد الحذف:**\n"
                f"💵 نقدي: `{balances['cash']:,.2f}` ج.م | 💳 فيزا: `{balances['visa']:,.2f}` ج.م\n"
                f"🏦 حصالة: `{balances['savings']:,.2f}` ج.م\n"
                f"💰 الإجمالي: `{balances['total']:,.2f}` ج.م"
            )

        elif act_type == "transfer":
            tx_ids = last_act.get("tx_ids", [])
            await delete_from_sheet(user_key, tx_ids)
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            return (
                f"🗑️ **تم إلغاء التحويل واسترجاع الرصيد بنجاح!**\n\n"
                f"🔄 **الإجراء الملغي:** {summary}\n"
                "─────────────────────\n"
                f"💼 **محفظتك بعد الإلغاء:**\n"
                f"💵 نقدي: `{balances['cash']:,.2f}` ج.م | 💳 فيزا: `{balances['visa']:,.2f}` ج.م\n"
                f"🏦 حصالة: `{balances['savings']:,.2f}` ج.م\n"
                f"💰 الإجمالي: `{balances['total']:,.2f}` ج.م"
            )

        elif act_type == "debt":
            debt_id = last_act.get("debt_id")
            await delete_debt(user_key, debt_id)
            return (
                f"🗑️ **تم إلغاء وحذف الدين من السجلات بنجاح!**\n\n"
                f"🤝 **الإجراء الملغي:** {summary}\n"
                f"🔖 كود الدين: `{debt_id}`"
            )

        elif act_type == "subscription":
            sub_id = last_act.get("sub_id")
            await delete_subscription(user_key, sub_id)
            return (
                f"🗑️ **تم إلغاء وحذف الاشتراك بنجاح!**\n\n"
                f"🔁 **الإجراء الملغي:** {summary}\n"
                f"🔖 كود الاشتراك: `{sub_id}`"
            )

        elif act_type == "split_bill":
            tx_ids = last_act.get("tx_ids", [])
            debt_ids = last_act.get("debt_ids", [])
            if tx_ids:
                await delete_from_sheet(user_key, tx_ids)
            for d_id in debt_ids:
                await delete_debt(user_key, d_id)
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            return (
                f"🗑️ **تم إلغاء تقسيم الفاتورة بالكامل بنجاح!**\n\n"
                f"💱 **الإجراء الملغي:** {summary}\n"
                "تم مسح نصيبك من المصروفات وإلغاء الديون المسجلة على الأصدقاء.\n"
                "─────────────────────\n"
                f"💼 **محفظتك الآن:** `{balances['total']:,.2f}` ج.م"
            )

        elif act_type == "budget":
            prev_b = float(last_act.get("prev_amount", 0.0))
            set_monthly_budget(user_key, prev_b)
            return (
                f"🎯 **تم إلغاء تعديل الميزانية الأخير!**\n\n"
                + (f"تم استرجاع الميزانية السابقة: `{prev_b:,.2f}` ج.م" if prev_b > 0 else "تم إلغاء نظام الميزانية.")
            )

        elif act_type == "goal":
            prev_g = last_act.get("prev_goal", {"title": "تحويش عام", "target": 0.0})
            set_savings_goal(user_key, prev_g.get("title", "تحويش عام"), prev_g.get("target", 0.0))
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            return (
                f"🎯 **تم إلغاء هدف الادخار الأخير!**\n\n"
                + format_savings_status(user_key, balances)
            )

    # مسار احتياطي: حذف أحدث معاملة من شيت الحسابات
    transactions = await fetch_all_transactions(user_key)
    if transactions:
        target = transactions[-1]
        await delete_from_sheet(user_key, [target["tx_id"]])
        remaining_txs = [t for t in transactions if t["tx_id"] != target["tx_id"]]
        balances = calculate_wallet_balance(remaining_txs)
        return (
            "🗑️ **تم مسح وإلغاء آخر معاملة بنجاح من Google Sheets!**\n\n"
            f"📝 **الوصف:** {target['description']}\n"
            f"💰 **المبلغ:** `{target['amount']:,.2f}` ج.م ({target['type']})\n"
            f"🔖 **كود المعاملة:** `{target['tx_id']}`\n"
            "─────────────────────\n"
            f"💼 **محفظتك بعد الحذف:**\n"
            f"💵 نقدي: `{balances['cash']:,.2f}` ج.م | 💳 فيزا: `{balances['visa']:,.2f}` ج.م\n"
            f"🏦 حصالة: `{balances['savings']:,.2f}` ج.م\n"
            f"💰 الإجمالي: `{balances['total']:,.2f}` ج.م"
        )

    return "⚠️ لا توجد أي عمليات أو معاملات سابقة لإلغائها!"


async def delete_last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    status = await update.effective_message.reply_text("جاري إلغاء وحذف آخر إجراء... ⚡🗑️")
    del_msg = await execute_undo_last_action(user_key)
    await status.edit_text(del_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    status = await update.effective_message.reply_text("جاري استدعاء المستشار المالي وقراءة حساباتك... 🧠⏳")
    transactions = await fetch_all_transactions(user_key)
    advice = await get_financial_advice(user_key, transactions)
    await status.edit_text(advice, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    transactions = await fetch_all_transactions(user_key)
    balances = calculate_wallet_balance(transactions)
    await update.effective_message.reply_text(
        format_wallet_message(user_key, balances),
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    register_active_chat(user_key, update.effective_chat.id)
    status = await update.effective_message.reply_text("🔄 جاري تحديث البيانات من Google Sheets... ⏳")
    clear_sheets_cache()
    transactions = await fetch_all_transactions(user_key)
    balances = calculate_wallet_balance(transactions)
    msg = (
        "🔄 **تم تحديث بيانات المحفظة بنجاح:**\n\n"
        + format_wallet_message(user_key, balances)
    )
    await status.edit_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    status = await update.effective_message.reply_text("جاري استخراج تقرير اليوم... ⏳")
    transactions = await fetch_all_transactions(user_key)
    report = generate_daily_report(transactions)
    await status.edit_text(report, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg = "📊 **اختر الفترة الزمنية لتقرير المصاريف:**"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("☀️ اليوم", callback_data="period:today:all"),
                InlineKeyboardButton("📅 الشهر الحالي", callback_data="period:this_month:all"),
            ],
            [
                InlineKeyboardButton("⏳ آخر 7 أيام", callback_data="period:last_week:all"),
                InlineKeyboardButton("🗓️ كل المصاريف", callback_data="period:all_time:all"),
            ],
            [
                InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
            ],
        ]
    )
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg = "📈 **اختر نوع الرسم البياني الذي تريده:**"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📉 مصاريف الشهر", callback_data="make_chart:مصروف:this_month:all"),
                InlineKeyboardButton("💰 مصادر دخل الشهر", callback_data="make_chart:دخل:this_month:all"),
            ],
            [
                InlineKeyboardButton("⚖️ مقارنة الدخل بالمصاريف", callback_data="make_chart:مقارنة:this_month:all"),
            ],
            [
                InlineKeyboardButton("⏳ مصاريف آخر 7 أيام", callback_data="make_chart:مصروف:last_week:all"),
                InlineKeyboardButton("🗓️ إجمالي الدخل الكلي", callback_data="make_chart:دخل:all_time:all"),
            ],
            [
                InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
            ]
        ]
    )
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    status = await update.effective_message.reply_text("جاري تجهيز كشف الحساب من Google Sheets... ⏳")
    await send_excel_to_user(update.effective_chat.id, user_key, context)
    try:
        await status.delete()
    except Exception:
        pass


async def set_budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    if not context.args:
        await update.effective_message.reply_text(
            "ℹ️ لضبط ميزانيتك الشهرية اكتب الأمر متبوعاً بالمبلغ، مثال:\n`/set_budget 5000`",
            parse_mode="Markdown",
        )
        return
    try:
        amount = float(context.args[0])
        prev_b = get_monthly_budget(user_key)
        set_monthly_budget(user_key, amount)
        log_user_action(user_key, {
            "action_type": "budget",
            "prev_amount": prev_b,
            "new_amount": amount,
            "summary": f"تحديد ميزانية شهرية بمبلغ {amount:,.2f} ج.م",
        })
        if amount > 0:
            await update.effective_message.reply_text(
                f"🎯 **تم تحديد ميزانيتك الشهرية بنجاح:** `{amount:,.2f}` ج.م\nسأقوم بتنبيهك تلقائياً عند استهلاك 70% و 90% منها!",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )
        else:
            await update.effective_message.reply_text("✅ تم إلغاء نظام الميزانية الشهرية.", reply_markup=get_main_keyboard())
    except ValueError:
        await update.effective_message.reply_text("⚠️ يرجى كتابة رقم صحيح للميزانية (مثال: `/set_budget 4500`).")


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    transactions = await fetch_all_transactions(user_key)
    msg = format_budget_status(user_key, transactions)
    budget = get_monthly_budget(user_key)
    buttons = []
    if budget > 0:
        buttons.append([InlineKeyboardButton("🗑️ إلغاء الميزانية الشهرية", callback_data="action:delete_budget")])
    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")])
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def delete_budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    prev_b = get_monthly_budget(user_key)
    if prev_b <= 0:
        await update.effective_message.reply_text("ℹ️ ليس لديك ميزانية شهرية محددة حالياً لإلغائها.", reply_markup=get_main_keyboard())
        return
    set_monthly_budget(user_key, 0.0)
    log_user_action(user_key, {
        "action_type": "budget",
        "prev_amount": prev_b,
        "new_amount": 0.0,
        "summary": f"إلغاء الميزانية الشهرية ({prev_b:,.2f} ج.م)",
    })
    await update.effective_message.reply_text(
        f"🗑️ **تم إلغاء الميزانية الشهرية بنجاح!**\n"
        f"الميزانية السابقة الملغاة: `{prev_b:,.2f}` ج.م\n\n"
        "💡 يمكنك تحديد ميزانية جديدة في أي وقت.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    transactions = await fetch_all_transactions(user_key)
    balances = calculate_wallet_balance(transactions)
    msg = format_savings_status(user_key, balances)
    curr_goal = get_savings_goal(user_key)
    buttons = []
    if curr_goal.get("target", 0) > 0:
        buttons.append([InlineKeyboardButton("🗑️ إلغاء هدف الادخار", callback_data="action:delete_goal")])
    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")])
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def delete_goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    curr_goal = get_savings_goal(user_key)
    if curr_goal.get("target", 0) <= 0:
        await update.effective_message.reply_text("ℹ️ ليس لديك هدف ادخار محدد حالياً لإلغائه.", reply_markup=get_main_keyboard())
        return
    prev_title = curr_goal.get("title", "هدف الادخار")
    prev_target = float(curr_goal.get("target", 0.0))
    set_savings_goal(user_key, "تحويش عام", 0.0)
    log_user_action(user_key, {
        "action_type": "goal",
        "prev_goal": curr_goal,
        "new_goal": {"title": "تحويش عام", "target": 0.0},
        "summary": f"إلغاء هدف الادخار: {prev_title} ({prev_target:,.2f} ج.م)",
    })
    transactions = await fetch_all_transactions(user_key)
    balances = calculate_wallet_balance(transactions)
    await update.effective_message.reply_text(
        f"🗑️ **تم إلغاء هدف الادخار بنجاح!**\n\n"
        f"🎯 الهدف الملغي: **{prev_title}** (`{prev_target:,.2f}` ج.م)\n"
        f"🏦 رصيدك في الحصالة لا زال محفوظاً: `{balances['savings']:,.2f}` ج.م\n\n"
        "💡 يمكنك تحديد هدف جديد في أي وقت.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )


async def set_goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(
            "ℹ️ لضبط هدف الادخار، اكتب الأمر متبوعاً بالاسم والمبلغ، مثال:\n`/set_goal لابتوب 20000`",
            parse_mode="Markdown",
        )
        return
    try:
        amount = float(context.args[-1])
        title = " ".join(context.args[:-1])
        prev_goal = get_savings_goal(user_key)
        set_savings_goal(user_key, title, amount)
        log_user_action(user_key, {
            "action_type": "goal",
            "prev_goal": prev_goal,
            "new_goal": {"title": title, "target": amount},
            "summary": f"تحديد هدف الادخار: {title} بمبلغ {amount:,.2f} ج.م",
        })
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await update.effective_message.reply_text(
            f"🎯 **تم تحديد هدف الادخار بنجاح!**\n\n" + format_savings_status(user_key, balances),
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except ValueError:
        await update.effective_message.reply_text("⚠️ يرجى التأكد من كتابة المبلغ في النهاية كرقم صحيح، مثال: `/set_goal لابتوب 20000`.")


async def debts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    debts = await fetch_debts(user_key)
    await update.effective_message.reply_text(format_debts_msg(debts), parse_mode="Markdown", reply_markup=get_nav_keyboard())


async def subs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    subs = await fetch_subscriptions(user_key)
    buttons = []
    for s in subs:
        buttons.append([
            InlineKeyboardButton(f"🗑️ إلغاء اشتراك {s['name']}", callback_data=f"delsub:{s['id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data="action:balance")])
    await update.effective_message.reply_text(format_subs_msg(subs), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def delete_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    subs = await fetch_subscriptions(user_key)
    if not subs:
        await update.effective_message.reply_text("ℹ️ لا توجد لديك أي اشتراكات مسجلة حالياً.", reply_markup=get_main_keyboard())
        return
    if context.args:
        sub_name = " ".join(context.args).strip()
        matched_sub = None
        norm_target = normalize_ar_search(sub_name)
        for s in subs:
            if norm_target in normalize_ar_search(s["name"]) or s["id"].lower() == sub_name.lower():
                matched_sub = s
                break
        if matched_sub:
            await delete_subscription(user_key, matched_sub["id"])
            log_user_action(user_key, {
                "action_type": "subscription",
                "sub_id": matched_sub["id"],
                "name": matched_sub["name"],
                "amount": matched_sub["amount"],
                "summary": f"إلغاء اشتراك {matched_sub['name']}",
            })
            await update.effective_message.reply_text(f"🗑️ تم إلغاء وحذف اشتراك **{matched_sub['name']}** بنجاح!", parse_mode="Markdown", reply_markup=get_main_keyboard())
            return

    buttons = [
        [InlineKeyboardButton(f"🗑️ إلغاء {s['name']} ({s['amount']:,.0f}ج)", callback_data=f"delsub:{s['id']}")]
        for s in subs
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
    await update.effective_message.reply_text("🔍 **اختر الاشتراك الذي تريد إلغاءه:**", reply_markup=InlineKeyboardMarkup(buttons))



async def add_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    if not context.args or len(context.args) < 3:
        await update.effective_message.reply_text(
            "ℹ️ لإضافة اشتراك اكتب الأمر كالتالي:\n`/add_sub نت 450 1` (الاسم ثم المبلغ ثم يوم الشهر)",
            parse_mode="Markdown"
        )
        return
    try:
        name = context.args[0]
        amount = float(context.args[1])
        day = int(context.args[2])
        s_id = await add_subscription(user_key, name, amount, day)
        log_user_action(user_key, {
            "action_type": "subscription",
            "sub_id": s_id or name,
            "name": name,
            "amount": amount,
            "due_day": day,
            "summary": f"اشتراك شهري {name} بمبلغ {amount:,.2f} ج.م يوم {day}",
        })
        await update.effective_message.reply_text(
            f"✅ تم إضافة اشتراك **{name}** بمبلغ `{amount:,.2f}` يوم {day} شهرياً بنجاح!",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except Exception:
        await update.effective_message.reply_text("⚠️ يرجى التأكد من كتابة المبلغ واليوم أرقاماً صحيحة.")


async def installments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    insts = fetch_installments(user_key)
    msg = format_installments_msg(insts)
    buttons = []
    for i in [x for x in insts if x.get("status") == "نشط"]:
        buttons.append([
            InlineKeyboardButton(f"💳 سداد {i['name']} ({i['monthly_amount']:,.0f}ج)", callback_data=f"payinst:{i['id']}"),
            InlineKeyboardButton(f"🗑️ إلغاء", callback_data=f"delinst:{i['id']}")
        ])
    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")])
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def add_installment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    if not context.args or len(context.args) < 3:
        await update.effective_message.reply_text(
            "ℹ️ لإضافة قسط جديد اكتب:\n`/add_installment الاسم المبلغ_الشهري عدد_الشهور [يوم_الاستحقاق] [المحفظة]`\n\n"
            "📌 **مثال:**\n`/add_installment تكييف 750 10 5`\n*(قسط تكييف 750ج لمدة 10 شهور يستحق يوم 5)*",
            parse_mode="Markdown"
        )
        return
    try:
        name = context.args[0]
        amt = float(context.args[1])
        total_m = int(context.args[2])
        due_day = int(context.args[3]) if len(context.args) > 3 else 1
        wallet = context.args[4] if len(context.args) > 4 else "نقدي"
        inst_id = add_installment(user_key, name, amt, total_m, due_day=due_day, wallet=wallet)
        tot_val = amt * total_m
        log_user_action(user_key, {
            "action_type": "installment_add",
            "inst_id": inst_id,
            "name": name,
            "monthly_amount": amt,
            "total_months": total_m,
            "summary": f"إضافة قسط {name} ({amt:,.2f} ج.م × {total_m} شهر)",
        })
        await update.effective_message.reply_text(
            f"✅ **تم تسجيل القسط بنجاح!**\n\n"
            f"📦 **السلعة/الالتزام:** {name}\n"
            f"💰 **القسط الشهري:** `{amt:,.2f}` ج.م ({wallet})\n"
            f"⏳ **المدة:** `{total_m}` شهور (إجمالي `{tot_val:,.2f}` ج.م)\n"
            f"📅 **يوم الاستحقاق:** يوم `{due_day}` من كل شهر\n\n"
            f"سأقوم بتذكيرك تلقائياً يوم استحقاقه مع زر للسداد المباشر.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except ValueError:
        await update.effective_message.reply_text("⚠️ يرجى التأكد من كتابة الأرقام بشكل صحيح (المبلغ، عدد الشهور، اليوم).")


async def pay_installment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    insts = fetch_installments(user_key)
    active_insts = [i for i in insts if i.get("status") == "نشط"]
    if not active_insts:
        await update.effective_message.reply_text("ℹ️ ليس لديك أي أقساط نشطة حالياً لسدادها.", reply_markup=get_main_keyboard())
        return
    if context.args:
        sub_name = " ".join(context.args).strip()
        matched = None
        norm_t = normalize_ar_search(sub_name)
        for i in active_insts:
            if norm_t in normalize_ar_search(i["name"]) or i["id"].lower() == sub_name.lower():
                matched = i
                break
        if matched:
            res = pay_installment(user_key, matched["id"])
            amt = float(matched["monthly_amount"])
            tx_item = [{
                "description": f"سداد قسط {matched['name']} ({res['paid_months']}/{res['total_months']})",
                "amount": amt,
                "type": "مصروف",
                "category": "أقساط والتزامات",
                "wallet": matched.get("wallet", "نقدي"),
            }]
            await add_transactions(user_key, tx_item)
            rem_m = max(0, res['total_months'] - res['paid_months'])
            comp = "🎉 **(مبروك! تم سداد القسط بالكامل!)**" if res['status'] == 'مكتمل' else f"متبقي `{rem_m}` قسط."
            await update.effective_message.reply_text(
                f"✅ **تم سداد قسط {matched['name']} بنجاح!**\n"
                f"💰 المبلغ: `{amt:,.2f}` ج.م\n"
                f"📊 التقدم: `{res['paid_months']}` من `{res['total_months']}` قسط\n{comp}",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )
            return

    buttons = [
        [InlineKeyboardButton(f"💳 سداد {i['name']} ({i['monthly_amount']:,.0f}ج)", callback_data=f"payinst:{i['id']}")]
        for i in active_insts
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
    await update.effective_message.reply_text("🔍 **اختر القسط الذي تريد سداده الآن:**", reply_markup=InlineKeyboardMarkup(buttons))


async def delete_installment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    insts = fetch_installments(user_key)
    if not insts:
        await update.effective_message.reply_text("ℹ️ ليس لديك أي أقساط مسجلة لإلغائها.", reply_markup=get_main_keyboard())
        return
    if context.args:
        sub_name = " ".join(context.args).strip()
        matched = None
        norm_t = normalize_ar_search(sub_name)
        for i in insts:
            if norm_t in normalize_ar_search(i["name"]) or i["id"].lower() == sub_name.lower():
                matched = i
                break
        if matched:
            delete_installment(user_key, matched["id"])
            log_user_action(user_key, {
                "action_type": "installment_delete",
                "inst_id": matched["id"],
                "name": matched["name"],
                "summary": f"إلغاء قسط {matched['name']}",
            })
            await update.effective_message.reply_text(f"🗑️ تم إلغاء وحذف قسط **{matched['name']}** بنجاح!", parse_mode="Markdown", reply_markup=get_main_keyboard())
            return

    buttons = [
        [InlineKeyboardButton(f"🗑️ إلغاء {i['name']}", callback_data=f"delinst:{i['id']}")]
        for i in [x for x in insts if x.get("status") == "نشط"]
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
    await update.effective_message.reply_text("🔍 **اختر القسط الذي تريد إلغاءه:**", reply_markup=InlineKeyboardMarkup(buttons))


async def set_cat_budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    if not context.args or len(context.args) < 2:
        cb = get_category_budgets(user_key)
        if cb:
            lines = ["🏷️ **ميزانيات التصنيفات الحالية:**"]
            for c, amt in cb.items():
                lines.append(f"• **{c}:** `{amt:,.2f}` ج.م شهرياً")
            lines.append("\nلتعديل أو إضافة ميزانية تصنيف: `/set_cat_budget التصنيف المبلغ`")
            lines.append("لحذف ميزانية تصنيف: `/delete_cat_budget التصنيف`")
            await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(
                "ℹ️ لتحديد ميزانية لتصنيف معين اكتب الأمر كالتالي:\n"
                "`/set_cat_budget طعام ومشروبات 3000`\n"
                "`/set_cat_budget تسوق 1500`",
                parse_mode="Markdown"
            )
        return
    try:
        amt = float(context.args[-1])
        cat = " ".join(context.args[:-1]).strip()
        set_category_budget(user_key, cat, amt)
        log_user_action(user_key, {
            "action_type": "cat_budget",
            "category": cat,
            "amount": amt,
            "summary": f"تحديد ميزانية {cat} بمبلغ {amt:,.2f} ج.م",
        })
        await update.effective_message.reply_text(
            f"🎯 **تم تحديد ميزانية شهرية لتصنيف ({cat}) بنجاح!**\n"
            f"💰 الحد الأقصى الشهري: `{amt:,.2f}` ج.م\n"
            f"سأقوم بتنبيهك تلقائياً عند الاقتراب من هذا الحد أثناء تسجيل المصاريف.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except ValueError:
        await update.effective_message.reply_text("⚠️ يرجى التأكد من كتابة المبلغ كرقم صحيح في النهاية.")


async def delete_cat_budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    cb = get_category_budgets(user_key)
    if not cb:
        await update.effective_message.reply_text("ℹ️ ليس لديك أي ميزانيات مخصصة للتصنيفات لإلغائها.", reply_markup=get_main_keyboard())
        return
    if context.args:
        cat = " ".join(context.args).strip()
        norm_c = normalize_ar_search(cat)
        matched_cat = None
        for k in cb.keys():
            if norm_c in normalize_ar_search(k):
                matched_cat = k
                break
        if matched_cat:
            delete_category_budget(user_key, matched_cat)
            await update.effective_message.reply_text(f"🗑️ تم إلغاء ميزانية تصنيف **{matched_cat}** بنجاح!", parse_mode="Markdown", reply_markup=get_main_keyboard())
            return

    buttons = [
        [InlineKeyboardButton(f"🗑️ إلغاء ميزانية {k}", callback_data=f"delcatb:{k}")]
        for k in cb.keys()
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
    await update.effective_message.reply_text("🔍 **اختر ميزانية التصنيف التي تريد إلغاءها:**", reply_markup=InlineKeyboardMarkup(buttons))


async def monthly_closing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_identifier(update.effective_user)
    status = await update.effective_message.reply_text("جاري استخراج وإعداد التقرير الختامي للشهر... ⏳")
    transactions = await fetch_all_transactions(user_key)
    target_year = None
    target_month = None
    if context.args and len(context.args) >= 2:
        try:
            target_month = int(context.args[0])
            target_year = int(context.args[1])
        except ValueError:
            pass
    elif context.args and len(context.args) == 1:
        try:
            target_month = int(context.args[0])
            target_year = get_now().year
        except ValueError:
            pass

    report = generate_monthly_closing_report(user_key, transactions, target_year=target_year, target_month=target_month)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 رسم بياني للمصاريف", callback_data="chart_p:last_month:مصروف")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")]
    ])
    await status.edit_text(report, parse_mode="Markdown", reply_markup=keyboard)


async def split_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تقسيم فاتورة أو حساب مشترك مع أصدقاء بالتساوي أو بمبالغ مخصصة لكل شخص."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    args = context.args

    if not args:
        guide_msg = (
            "💱 **طريقة تقسيم الفواتير والحساب المشترك:**\n\n"
            "✨ **1. بالطريقة الذكية (اكتبها بالعامية في أي رسالة):**\n"
            "• `فاتورة كافيه 600 أنا دفعتها نقدي: أنا عليا 200 وأحمد 300 ومصطفى 100`\n"
            "• `اتغدينا أنا ومحمد وعلي بـ 1000، أنا دفعت 700 وعلي دفع 300، وأنا عليا 200 ومحمد 500 وعلي 300`\n"
            "• `فاتورة 300 قسمها عليا أنا ومحمد بالتساوي`\n\n"
            "⚡ **2. بالأمر السريع:**\n"
            "• `/split 600 3` (تقسيم 600 على 3 بالتساوي)\n"
            "• `/split 600 أحمد مصطفى` (تقسيم بالتساوي وتسجيل ديون على أصدقائك)"
        )
        await update.effective_message.reply_text(
            guide_msg,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return

    full_text = "تقسيم فاتورة: " + " ".join(args)
    status = await update.effective_message.reply_text("جاري حساب وتقسيم الفاتورة... 💱⏳")
    data = await analyze_user_request(full_text, is_audio=False)
    await process_user_input(update, context, data, status)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دليل استخدام البوت الشامل."""
    help_text = (
        "📖 **دليل استخدام محفظتك الذكية:**\n\n"
        "✒️ **التسجيل الفوري (اكتب بالعامية وهيتسجل):**\n"
        "• `صرفت 50 مواصلات`\n"
        "• `قبضت 5000 مرتب فيزا`\n"
        "• `استلفت من أحمد 1000`\n"
        "• `حول من الحصالة للنقدي 500`\n"
        "• `امسح آخر عملية`\n\n"
        "🎙️ **الرسائل الصوتية:** ابعت فويس وهيتحلل ويتسجل!\n"
        "📸 **مسح الفواتير:** صوّر الفاتورة وابعتها!\n\n"
        "📋 **الأوامر المتاحة:**\n"
        "/start - 🚀 بدء التشغيل\n"
        "/balance - 💼 رصيد المحفظة\n"
        "/today - ☀️ ملخص اليوم\n"
        "/report - 📊 تقارير المصاريف\n"
        "/chart - 📈 رسوم بيانية\n"
        "/advice - 💡 مستشار مالي AI\n"
        "/debts - 📋 الديون والسلفيات\n"
        "/subs - 🔁 الاشتراكات الشهرية\n"
        "/add\\_sub - ➕ إضافة اشتراك\n"
        "/export - 📁 كشف حساب Excel\n"
        "/set\\_budget - 🎯 تحديد ميزانية شهرية\n"
        "/budget - 💰 موقف الميزانية\n"
        "/set\\_cat\\_budget - 🏷️ ميزانية مخصصة لتصنيف معين\n"
        "/installments - 💳 متابعة الأقساط والالتزامات\n"
        "/add\\_installment - ➕ إضافة قسط جديد\n"
        "/monthly\\_closing - 🏆 تقرير تقفيل الشهر الختامي\n"
        "/set\\_goal - 🎯 هدف ادخار\n"
        "/goal - 🏦 متابعة الحصالة\n"
        "/yearly - 📅 تقرير سنوي شامل\n"
        "/compare - ⚖️ مقارنة شهرية\n"
        "/set\\_salary - 💵 تسجيل مرتب تلقائي\n"
        "/delete\\_last - 🗑️ مسح أو التراجع عن آخر معاملة\n"
        "/delete\\_budget - 🗑️ إلغاء الميزانية الشهرية\n"
        "/delete\\_goal - 🗑️ إلغاء هدف الادخار الحالي\n"
        "/delete\\_sub - 🗑️ إلغاء وحذف اشتراك دوري\n"
        "/help - 📖 هذا الدليل\n\n"
        "💡 **نصائح:**\n"
        "• يمكنك إرسال عدة عمليات في رسالة واحدة\n"
        "• اكتب 'امسح' + الوصف أو المبلغ للحذف السريع\n"
        "• المحفظة الافتراضية 'نقدي' إلا لو حددت غيرها"
    )
    await update.effective_message.reply_text(help_text, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def yearly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    status = await update.effective_message.reply_text("جاري تجهيز التقرير السنوي الشامل... 📊⏳")
    transactions = await fetch_all_transactions(user_key)
    year = int(context.args[0]) if context.args else None
    report = generate_yearly_report(transactions, year=year)
    await status.edit_text(report, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    status = await update.effective_message.reply_text("جاري تجهيز المقارنة الشهرية... ⚖️⏳")
    transactions = await fetch_all_transactions(user_key)
    month1 = int(context.args[0]) if context.args and len(context.args) >= 1 else None
    month2 = int(context.args[1]) if context.args and len(context.args) >= 2 else None
    report = generate_monthly_comparison(transactions, month1=month1, month2=month2)
    await status.edit_text(report, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def set_salary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    if not context.args or len(context.args) < 2:
        ri = get_recurring_income(user_key)
        if ri and ri.get("amount", 0) > 0:
            await update.effective_message.reply_text(
                f"💵 **الدخل الشهري التلقائي الحالي:**\n\n"
                f"📝 **الوصف:** {ri.get('description', 'مرتب')}\n"
                f"💰 **المبلغ:** `{ri['amount']:,.2f}` ج.م\n"
                f"📅 **يوم التسجيل:** {ri.get('day', 1)} من كل شهر\n"
                f"💼 **المحفظة:** {ri.get('wallet', 'فيزا')}\n\n"
                "لتعديله: `/set_salary المبلغ اليوم [الوصف] [المحفظة]`\n"
                "لإلغائه: `/set_salary 0 0`",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )
        else:
            await update.effective_message.reply_text(
                "ℹ️ **لتسجيل مرتب تلقائي شهري:**\n\n"
                "`/set_salary المبلغ اليوم [الوصف] [المحفظة]`\n\n"
                "📌 **أمثلة:**\n"
                "• `/set_salary 8000 27` → 8000 ج.م يوم 27 (فيزا)\n"
                "• `/set_salary 5000 1 راتب نقدي` → 5000 يوم 1 (نقدي)\n"
                "• `/set_salary 0 0` → إلغاء المرتب التلقائي",
                parse_mode="Markdown",
            )
        return
    try:
        amount = float(context.args[0])
        day = int(context.args[1])
        description = context.args[2] if len(context.args) > 2 else "مرتب شهري"
        wallet = context.args[3] if len(context.args) > 3 else "فيزا"

        if amount <= 0:
            set_recurring_income(user_key, 0, "", "", 0)
            await update.effective_message.reply_text(
                "✅ تم إلغاء الدخل الشهري التلقائي.",
                reply_markup=get_main_keyboard(),
            )
            return

        set_recurring_income(user_key, amount, description, wallet, day)
        await update.effective_message.reply_text(
            f"✅ **تم تسجيل الدخل الشهري التلقائي بنجاح!**\n\n"
            f"📝 **الوصف:** {description}\n"
            f"💰 **المبلغ:** `{amount:,.2f}` ج.م\n"
            f"📅 **يتسجل تلقائياً يوم:** {day} من كل شهر\n"
            f"💼 **المحفظة:** {wallet}",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except (ValueError, IndexError):
        await update.effective_message.reply_text(
            "⚠️ تأكد من كتابة المبلغ واليوم بالأرقام.\n"
            "مثال: `/set_salary 8000 27`",
            parse_mode="Markdown",
        )


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة التصنيفات المعتمدة للمستخدم."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    cats = get_user_categories(user_key)
    msg = "🏷️ **تصنيفات المصاريف المعتمدة لديك:**\n\n"
    for idx, c in enumerate(cats, 1):
        msg += f"{idx}. {c}\n"
    msg += (
        "\n➕ لإضافة تصنيف جديد: `/add_category اسم_التصنيف`\n"
        "➖ لحذف تصنيف مخصص: `/remove_category اسم_التصنيف`"
    )
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def add_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة تصنيف مخصص جديد."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    if not context.args:
        await update.effective_message.reply_text(
            "ℹ️ اكتب اسم التصنيف بعد الأمر، مثال:\n`/add_category صيانة السيارات`",
            parse_mode="Markdown",
        )
        return
    new_cat = " ".join(context.args).strip()
    if add_user_category(user_key, new_cat):
        await update.effective_message.reply_text(
            f"✅ تم إضافة تصنيف **{new_cat}** بنجاح!\nسيتعرف عليه الذكاء الاصطناعي في رسائلك القادمة تلقائياً.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    else:
        await update.effective_message.reply_text(f"⚠️ التصنيف **{new_cat}** موجود بالفعل مسبقاً!")


async def remove_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف تصنيف مخصص."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    if not context.args:
        await update.effective_message.reply_text("ℹ️ اكتب اسم التصنيف المراد حذفه، مثال:\n`/remove_category صيانة السيارات`", parse_mode="Markdown")
        return
    target_cat = " ".join(context.args).strip()
    if remove_user_category(user_key, target_cat):
        await update.effective_message.reply_text(f"✅ تم حذف تصنيف **{target_cat}** بنجاح.", parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.effective_message.reply_text(f"⚠️ التصنيف **{target_cat}** غير موجود أو من التصنيفات الأساسية التي لا يمكن حذفها.", parse_mode="Markdown")


async def set_pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحديد أو إلغاء رمز PIN لقفل البوت لحماية الخصوصية."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    user_key = get_user_identifier(user)
    if not context.args:
        curr_pin = get_user_pin(user_key)
        status = "مفعل 🔒" if curr_pin else "معطل 🔓"
        await update.effective_message.reply_text(
            f"🔐 **قفل البوت بـ PIN (حماية الخصوصية):**\n"
            f"الحالة الحالية: **{status}**\n\n"
            f"• لتفعيل أو تغيير PIN: `/set_pin 1234`\n"
            f"• لإلغاء القفل: `/set_pin 0`",
            parse_mode="Markdown",
        )
        return
    pin_code = str(context.args[0]).strip()
    if pin_code == "0" or pin_code.lower() in ["off", "disable", "الغاء"]:
        set_user_pin(user_key, "")
        context.user_data["pin_unlocked"] = True
        await update.effective_message.reply_text("🔓 تم إلغاء قفل PIN بنجاح. البوت مفتوح دائماً.", reply_markup=get_main_keyboard())
    elif len(pin_code) >= 4 and pin_code.isdigit():
        set_user_pin(user_key, pin_code)
        context.user_data["pin_unlocked"] = True
        await update.effective_message.reply_text(f"🔒 **تم تفعيل قفل PIN بنجاح!**\nالرمز السري الخاص بك: `{pin_code}`\nاحفظ هذا الرمز جيداً لفتح البوت في أي وقت.", parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.effective_message.reply_text("⚠️ يجب أن يتكون رمز الـ PIN من 4 أرقام على الأقل. مثال: `/set_pin 1234`")


# ==========================================
# 3. المعالجة المركزية لمدخلات المستخدم
# ==========================================
async def process_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict, status_msg):
    intent = data.get("intent")
    user_key = get_user_identifier(update.effective_user)
    date_str = get_now().strftime("%Y-%m-%d")

    if intent == "error":
        await status_msg.edit_text("⚠️ تعذر الاتصال بنماذج الذكاء الاصطناعي حالياً.")
        return

    if intent == "batch_transactions":
        intent = "multi_actions"
        data["actions"] = [
            {"action": "transaction", **t} for t in data.get("transactions", [])
        ]

    if intent == "multi_actions":
        actions = data.get("actions", [])
        if not actions:
            await status_msg.edit_text("🔍 لم أستطع التقاط تفاصيل العمليات بدقة من رسالتك.")
            return

        summary_lines = []
        pending_txs = []

        created_split_debt_ids = []
        for act in actions:
            act_type = act.get("action", "")

            # 1. سلف أو دين مع المقاصة والتصفية التلقائية
            if act_type == "debt_add":
                person = act.get("person", "شخص")
                amt = float(act.get("amount", 0))
                d_type = act.get("debt_type", "عليا")
                if amt > 0:
                    res_debt = await add_debt(user_key, person, amt, d_type)
                    label = "استلفت من" if d_type == "عليا" else "سلّفت"
                    if res_debt == "SETTLED":
                        summary_lines.append(f"• 🤝 **تسوية دين:** {label} **{person}** بمبلغ `{amt:,.2f}` ج.م (تمت المقاصة وتصفية الحساب بالكامل!)")
                    else:
                        summary_lines.append(f"• 🤝 **دين:** {label} **{person}** بمبلغ `{amt:,.2f}` ج.م (تم تسجيله وحساب الصافي)")
                        log_user_action(user_key, {
                            "action_type": "debt",
                            "debt_id": res_debt,
                            "person": person,
                            "amount": amt,
                            "debt_type": d_type,
                            "summary": f"{label} {person} بمبلغ {amt:,.2f} ج.م",
                        })

            # 2. تحويل بين المحافظ
            elif act_type == "transfer":
                amt = float(act.get("amount", 0))
                from_w = normalize_wallet(act.get("from_wallet", "حصالة"))
                to_w = normalize_wallet(act.get("to_wallet", "نقدي"))
                if amt > 0 and from_w != to_w:
                    t1 = f"TX-{uuid.uuid4().hex[:6].upper()}"
                    t2 = f"TX-{uuid.uuid4().hex[:6].upper()}"
                    await add_to_sheet(user_key, f"تحويل إلى {to_w}", amt, "مصروف", "تحويلات", date_str, from_w, t1)
                    await add_to_sheet(user_key, f"تحويل من {from_w}", amt, "دخل", "تحويلات", date_str, to_w, t2)
                    summary_lines.append(f"• 🔄 **تحويل:** `{amt:,.2f}` ج.م (من {from_w} إلى {to_w})")
                    log_user_action(user_key, {
                        "action_type": "transfer",
                        "tx_ids": [t1, t2],
                        "amount": amt,
                        "from_wallet": from_w,
                        "to_wallet": to_w,
                        "summary": f"تحويل {amt:,.2f} ج.م من {from_w} إلى {to_w}",
                    })

            # 3. سداد دين
            elif act_type == "debt_pay":
                person = act.get("person", "")
                amt = float(act.get("amount", 0))
                wallet = normalize_wallet(act.get("wallet", "نقدي"))
                if amt > 0 and person:
                    res = await pay_debt(user_key, person, amt)
                    if res:
                        debt_type = res.get("type", "عليا")
                        if debt_type == "عليا":
                            await add_to_sheet(user_key, f"سداد دين لـ {res['person']}", amt, "مصروف", "سداد ديون", date_str, wallet)
                        else:
                            await add_to_sheet(user_key, f"استرداد دين من {res['person']}", amt, "دخل", "تحصيل ديون", date_str, wallet)
                        summary_lines.append(f"• 💰 **سداد دين:** دفع `{amt:,.2f}` ج.م لـ **{person}** ({wallet})")
                        log_user_action(user_key, {
                            "action_type": "debt_pay",
                            "person": person,
                            "amount": amt,
                            "wallet": wallet,
                            "summary": f"سداد دين لـ {person} بمبلغ {amt:,.2f} ج.م",
                        })
                    else:
                        summary_lines.append(f"• ⚠️ لم أجد دين نشط باسم **{person}** لتسجيل سداده")

            # 4. معاملة مالية عادية
            elif act_type == "transaction":
                pending_txs.append(act)

            # 5. إضافة اشتراك شهري دوري تلقائياً
            elif act_type == "sub_add":
                name = act.get("name", "اشتراك")
                amt = float(act.get("amount", 0))
                day = int(act.get("due_day", 1))
                wallet = normalize_wallet(act.get("wallet", "نقدي"))
                if amt > 0:
                    s_id = await add_subscription(user_key, name, amt, day, wallet)
                    summary_lines.append(f"• 🔁 **إضافة اشتراك:** **{name}** بمبلغ `{amt:,.2f}` ج.م يوم {day} شهرياً ({wallet})")
                    log_user_action(user_key, {
                        "action_type": "subscription",
                        "sub_id": s_id or name,
                        "name": name,
                        "amount": amt,
                        "due_day": day,
                        "summary": f"اشتراك {name} بمبلغ {amt:,.2f} ج.م يوم {day}",
                    })

            # 6. تحديد هدف ادخار في الحصالة
            elif act_type == "set_goal":
                goal_title = act.get("goal_title", "هدف ادخار")
                amt = float(act.get("amount", 0))
                if amt > 0:
                    prev_g = get_savings_goal(user_key)
                    set_savings_goal(user_key, goal_title, amt)
                    summary_lines.append(f"• 🎯 **تحديد هدف ادخار:** **{goal_title}** بمبلغ `{amt:,.2f}` ج.م")
                    log_user_action(user_key, {
                        "action_type": "goal",
                        "prev_goal": prev_g,
                        "new_goal": {"title": goal_title, "target": amt},
                        "summary": f"تحديد هدف ادخار {goal_title} بمبلغ {amt:,.2f} ج.م",
                    })

            # 7. إضافة قسط شهري جديد
            elif act_type == "installment_add":
                name = act.get("name", "قسط")
                monthly_amt = float(act.get("monthly_amount", 0))
                total_m = int(act.get("total_months", 1))
                due_d = int(act.get("due_day", 1))
                w = normalize_wallet(act.get("wallet", "نقدي"))
                if monthly_amt > 0 and total_m > 0:
                    i_id = add_installment(user_key, name, monthly_amt, total_m, due_day=due_d, wallet=w)
                    tot_val = monthly_amt * total_m
                    summary_lines.append(f"• 💳 **إضافة قسط جديد:** **{name}** بمبلغ `{monthly_amt:,.2f}` ج.م شهرياً لمدة `{total_m}` شهور (يوم {due_d}) بإجمالي `{tot_val:,.2f}` ج.م ({w})")
                    log_user_action(user_key, {
                        "action_type": "installment_add",
                        "inst_id": i_id,
                        "name": name,
                        "monthly_amount": monthly_amt,
                        "total_months": total_m,
                        "summary": f"إضافة قسط {name} ({monthly_amt:,.2f} ج.م × {total_m} شهر)",
                    })

            # 8. سداد قسط شهري
            elif act_type == "installment_pay":
                inst_name = act.get("name", "").strip()
                w = normalize_wallet(act.get("wallet", "نقدي"))
                insts = fetch_installments(user_key)
                matched_inst = None
                if inst_name:
                    norm_target = normalize_ar_search(inst_name)
                    for i in insts:
                        if i.get("status") == "نشط" and (norm_target in normalize_ar_search(i["name"]) or i["id"].lower() == inst_name.lower()):
                            matched_inst = i
                            break
                if not matched_inst:
                    active_ones = [i for i in insts if i.get("status") == "نشط"]
                    if len(active_ones) == 1:
                        matched_inst = active_ones[0]

                if matched_inst:
                    res = pay_installment(user_key, matched_inst["id"])
                    amt = float(matched_inst["monthly_amount"])
                    tx_item = {
                        "description": f"سداد قسط {matched_inst['name']} ({res['paid_months']}/{res['total_months']})",
                        "amount": amt,
                        "type": "مصروف",
                        "category": "أقساط والتزامات",
                        "wallet": w,
                        "date": act.get("date") or get_now().strftime("%Y-%m-%d"),
                    }
                    pending_txs.append(tx_item)
                    rem_count = max(0, res['total_months'] - res['paid_months'])
                    comp_badge = " 🎉 (تم اكتمال سداد القسط بالكامل!)" if res['status'] == 'مكتمل' else f" (متبقي {rem_count} قسط)"
                    summary_lines.append(f"• 💳 **سداد قسط:** **{matched_inst['name']}** بمبلغ `{amt:,.2f}` ج.م [قسط {res['paid_months']} من {res['total_months']}]{comp_badge}")
                    log_user_action(user_key, {
                        "action_type": "installment_pay",
                        "inst_id": matched_inst["id"],
                        "name": matched_inst["name"],
                        "amount": amt,
                        "summary": f"سداد قسط {matched_inst['name']} بمبلغ {amt:,.2f} ج.م",
                    })
                else:
                    summary_lines.append(f"• ⚠️ لم أجد قسطاً نشطاً باسم **{inst_name}** لسداده")

            # 9. ميزانية مخصصة لتصنيف
            elif act_type == "set_category_budget":
                cat = act.get("category", "").strip()
                amt = float(act.get("amount", 0))
                if cat and amt > 0:
                    set_category_budget(user_key, cat, amt)
                    summary_lines.append(f"• 🏷️ **تحديد ميزانية تصنيف:** **{cat}** بمبلغ `{amt:,.2f}` ج.م شهرياً")
                    log_user_action(user_key, {
                        "action_type": "cat_budget",
                        "category": cat,
                        "amount": amt,
                        "summary": f"تحديد ميزانية {cat} بمبلغ {amt:,.2f} ج.م",
                    })

            # 7. تقسيم فاتورة وحساب مشترك (مخصص أو بالتساوي)
            elif act_type == "split_bill":
                total_amt = float(act.get("total_amount", 0))
                desc = act.get("description", "فاتورة مشتركة")
                wallet = normalize_wallet(act.get("wallet", "نقدي"))
                cat = act.get("category", "طعام ومشروبات")
                participants = act.get("participants", [])
                user_paid = float(act.get("user_paid", 0))
                user_share = float(act.get("user_share", 0))

                if total_amt > 0:
                    if not participants:
                        p_count = int(act.get("people_count", 2))
                        if p_count < 2:
                            p_count = 2
                        user_share = round(total_amt / p_count, 2)
                        if user_paid <= 0:
                            user_paid = total_amt
                    else:
                        if user_share <= 0:
                            sum_shares = sum(float(p.get("share", 0)) for p in participants)
                            if total_amt > sum_shares:
                                user_share = round(total_amt - sum_shares, 2)
                            else:
                                user_share = round(total_amt / (len(participants) + 1), 2)
                        if user_paid <= 0 and not any(float(p.get("paid", 0)) > 0 for p in participants):
                            user_paid = total_amt

                    # تسجيل نصيب المستخدم الفعلي كمصروف في الشيت
                    if user_share > 0:
                        my_tx = {
                            "amount": user_share,
                            "description": f"نصيبي في {desc}",
                            "type": "مصروف",
                            "category": cat,
                            "wallet": wallet,
                        }
                        pending_txs.append(my_tx)

                    # حساب الديون والتسويات بين المستخدم والأصدقاء
                    debts_lines = []
                    details_lines = []
                    user_net = user_paid - user_share

                    for p in participants:
                        p_name = str(p.get("name", "")).strip()
                        if not p_name or p_name in ["أنا", "انا", "نفسي"]:
                            continue
                        p_paid = float(p.get("paid", 0))
                        p_share = float(p.get("share", 0))
                        net_p = p_paid - p_share

                        details_lines.append(f"• **{p_name}**: دفع `{p_paid:,.2f}` | عليه `{p_share:,.2f}`")

                        if net_p < 0:
                            amt_owed = abs(net_p)
                            if user_paid > 0:
                                d_id = await add_debt(user_key, p_name, amt_owed, "ليا")
                                if d_id and d_id != "SETTLED":
                                    created_split_debt_ids.append(d_id)
                                debts_lines.append(f"دين لك على **{p_name}**: `{amt_owed:,.2f}` ج.م")
                        elif net_p > 0:
                            amt_surplus = net_p
                            if user_net < 0:
                                owed_by_user = min(amt_surplus, abs(user_net))
                                d_id = await add_debt(user_key, p_name, owed_by_user, "عليا")
                                if d_id and d_id != "SETTLED":
                                    created_split_debt_ids.append(d_id)
                                debts_lines.append(f"دين عليك لـ **{p_name}**: `{owed_by_user:,.2f}` ج.م")

                    split_info = (
                        f"• 💱 **تقسيم فاتورة ({desc}):** إجمالي `{total_amt:,.2f}` ج.م\n"
                        f"  └ **أنت:** دفعت `{user_paid:,.2f}` ج.م | نصيبك `{user_share:,.2f}` ج.م (مصروف {wallet})"
                    )
                    if details_lines:
                        split_info += "\n  └ " + "\n  └ ".join(details_lines)
                    if debts_lines:
                        split_info += "\n  └ 🤝 تم تسجيل: " + "، ".join(debts_lines)
                    summary_lines.append(split_info)

        created_tx_ids = []
        if pending_txs:
            created_tx_ids = await add_transactions(user_key, pending_txs)
            for i in pending_txs:
                badge = "💳" if i.get("wallet") == "فيزا" else ("🏦" if i.get("wallet") == "حصالة" else "💵")
                date_note = f" 📅 `{i.get('date')}`" if i.get("date") and i.get("date") != get_now().strftime("%Y-%m-%d") else ""
                summary_lines.append(f"• 📝 **{i.get('description', 'عملية')}:** `{float(i.get('amount', 0)):,.2f}` ج.م ({badge} {i.get('wallet', 'نقدي')}){date_note}")

        if any(act.get("action") == "split_bill" for act in actions):
            log_user_action(user_key, {
                "action_type": "split_bill",
                "tx_ids": created_tx_ids,
                "debt_ids": created_split_debt_ids,
                "summary": "تقسيم فاتورة مشتركة",
            })
        elif created_tx_ids:
            log_user_action(user_key, {
                "action_type": "transaction",
                "tx_ids": created_tx_ids,
                "summary": f"معاملات ({len(created_tx_ids)}): {', '.join(t.get('description', 'معاملة') for t in pending_txs)} بمبلغ إجمالي {sum(float(t.get('amount', 0)) for t in pending_txs):,.2f} ج.م",
            })


        txs = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(txs)
        budget_alert = check_budget_alert(user_key, txs) if any(act.get("type") == "مصروف" for act in actions) else ""

        img_note = "\n🖼️ **تم حفظ صورة الفاتورة في Google Sheets بنجاح!**\n" if data.get("image_url") else ""

        reply = (
            f"✅ **تم تنفيذ العمليات بنجاح في Google Sheets!** ({len(summary_lines)} عملية)\n\n"
            + "\n".join(summary_lines)
            + img_note
            + "\n─────────────────────\n"
            f"💼 **محفظتك الآن:**\n"
            f"💵 نقدي: `{balances['cash']:,.2f}` ج.م | 💳 فيزا: `{balances['visa']:,.2f}` ج.م\n"
            f"🏦 حصالة: `{balances['savings']:,.2f}` ج.م\n"
            f"💰 الإجمالي: `{balances['total']:,.2f}` ج.م"
            + budget_alert
        )

        buttons = []
        if data.get("image_url"):
            buttons.append([
                InlineKeyboardButton("🧾 عرض صورة الفاتورة المحفوظة", url=data["image_url"])
            ])
        if created_tx_ids:
            if len(created_tx_ids) == 1:
                buttons.append([
                    InlineKeyboardButton("↩️ تراجع عن العملية", callback_data=f"undo_tx:{created_tx_ids[0]}"),
                    InlineKeyboardButton("✏️ تعديل المبلغ", callback_data=f"editamt:{created_tx_ids[0]}"),
                ])
            else:
                buttons.append([
                    InlineKeyboardButton("↩️ تراجع عن الكل", callback_data=f"undo_all:{','.join(created_tx_ids)}"),
                    InlineKeyboardButton("✏️ تعديل آخر مبلغ", callback_data=f"editamt:{created_tx_ids[-1]}"),
                ])
        buttons.append([
            InlineKeyboardButton("📋 الديون المصفاة", callback_data="action:debts"),
            InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance"),
        ])
        await status_msg.edit_text(reply, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


    elif intent == "query_debts":
        person = data.get("person", "").strip()
        debts = await fetch_debts(user_key)
        await status_msg.edit_text(format_debts_msg(debts, target_person=person), parse_mode="Markdown", reply_markup=get_nav_keyboard())

    elif intent == "query_subs":
        subs = await fetch_subscriptions(user_key)
        await status_msg.edit_text(format_subs_msg(subs), parse_mode="Markdown", reply_markup=get_nav_keyboard())

    elif intent in ["delete_last_action", "delete_transaction"] and data.get("is_last"):
        del_msg = await execute_undo_last_action(user_key)
        await status_msg.edit_text(del_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif intent == "delete_budget":
        prev_b = get_monthly_budget(user_key)
        if prev_b <= 0:
            await status_msg.edit_text("ℹ️ ليس لديك ميزانية شهرية محددة حالياً لإلغائها.", reply_markup=get_main_keyboard())
        else:
            set_monthly_budget(user_key, 0.0)
            log_user_action(user_key, {
                "action_type": "budget",
                "prev_amount": prev_b,
                "new_amount": 0.0,
                "summary": f"إلغاء الميزانية الشهرية ({prev_b:,.2f} ج.م)",
            })
            await status_msg.edit_text(
                f"🗑️ **تم إلغاء الميزانية الشهرية بنجاح!**\n"
                f"الميزانية السابقة الملغاة: `{prev_b:,.2f}` ج.م\n\n"
                "💡 يمكنك تحديد ميزانية جديدة في أي وقت.",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )

    elif intent == "delete_goal":
        curr_goal = get_savings_goal(user_key)
        if curr_goal.get("target", 0) <= 0:
            await status_msg.edit_text("ℹ️ ليس لديك هدف ادخار محدد حالياً لإلغائه.", reply_markup=get_main_keyboard())
        else:
            prev_title = curr_goal.get("title", "هدف الادخار")
            prev_target = float(curr_goal.get("target", 0.0))
            set_savings_goal(user_key, "تحويش عام", 0.0)
            log_user_action(user_key, {
                "action_type": "goal",
                "prev_goal": curr_goal,
                "new_goal": {"title": "تحويش عام", "target": 0.0},
                "summary": f"إلغاء هدف الادخار: {prev_title} ({prev_target:,.2f} ج.م)",
            })
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            await status_msg.edit_text(
                f"🗑️ **تم إلغاء هدف الادخار بنجاح!**\n\n"
                f"🎯 الهدف الملغي: **{prev_title}** (`{prev_target:,.2f}` ج.م)\n"
                f"🏦 رصيدك في الحصالة لا زال محفوظاً: `{balances['savings']:,.2f}` ج.م\n\n"
                "💡 يمكنك تحديد هدف جديد في أي وقت.",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )

    elif intent == "delete_subscription":
        subs = await fetch_subscriptions(user_key)
        if not subs:
            await status_msg.edit_text("ℹ️ لا توجد لديك أي اشتراكات مسجلة حالياً لإلغائها.", reply_markup=get_main_keyboard())
        else:
            sub_name = (data.get("name") or "").strip()
            matched_sub = None
            if sub_name:
                norm_target = normalize_ar_search(sub_name)
                for s in subs:
                    if norm_target in normalize_ar_search(s["name"]) or s["id"].lower() == sub_name.lower():
                        matched_sub = s
                        break
            if matched_sub:
                await delete_subscription(user_key, matched_sub["id"])
                log_user_action(user_key, {
                    "action_type": "subscription",
                    "sub_id": matched_sub["id"],
                    "name": matched_sub["name"],
                    "amount": matched_sub["amount"],
                    "summary": f"إلغاء اشتراك {matched_sub['name']}",
                })
                await status_msg.edit_text(
                    f"🗑️ تم إلغاء وحذف اشتراك **{matched_sub['name']}** بنجاح!",
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard(),
                )
            else:
                buttons = [
                    [InlineKeyboardButton(f"🗑️ إلغاء {s['name']} ({s['amount']:,.0f}ج)", callback_data=f"delsub:{s['id']}")]
                    for s in subs
                ]
                buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
    elif intent == "query_installments":
        insts = fetch_installments(user_key)
        msg = format_installments_msg(insts)
        buttons = []
        for i in [x for x in insts if x.get("status") == "نشط"]:
            buttons.append([
                InlineKeyboardButton(f"💳 سداد {i['name']} ({i['monthly_amount']:,.0f}ج)", callback_data=f"payinst:{i['id']}"),
                InlineKeyboardButton("🗑️ إلغاء", callback_data=f"delinst:{i['id']}")
            ])
        buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")])
        await status_msg.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif intent == "delete_installment":
        insts = fetch_installments(user_key)
        if not insts:
            await status_msg.edit_text("ℹ️ ليس لديك أي أقساط مسجلة لإلغائها.", reply_markup=get_main_keyboard())
        else:
            name = (data.get("name") or "").strip()
            matched = None
            if name:
                norm_target = normalize_ar_search(name)
                for i in insts:
                    if norm_target in normalize_ar_search(i["name"]) or i["id"].lower() == name.lower():
                        matched = i
                        break
            if matched:
                delete_installment(user_key, matched["id"])
                log_user_action(user_key, {
                    "action_type": "installment_delete",
                    "inst_id": matched["id"],
                    "name": matched["name"],
                    "summary": f"إلغاء قسط {matched['name']}",
                })
                await status_msg.edit_text(f"🗑️ تم إلغاء وحذف قسط **{matched['name']}** بنجاح!", parse_mode="Markdown", reply_markup=get_main_keyboard())
            else:
                buttons = [
                    [InlineKeyboardButton(f"🗑️ إلغاء {i['name']}", callback_data=f"delinst:{i['id']}")]
                    for i in [x for x in insts if x.get("status") == "نشط"]
                ]
                buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
                await status_msg.edit_text("🔍 **اختر القسط الذي تريد إلغاءه:**", reply_markup=InlineKeyboardMarkup(buttons))

    elif intent == "delete_category_budget":
        cat = data.get("category", "").strip()
        cb = get_category_budgets(user_key)
        matched_cat = None
        if cat:
            norm_c = normalize_ar_search(cat)
            for k in cb.keys():
                if norm_c in normalize_ar_search(k):
                    matched_cat = k
                    break
        if matched_cat:
            delete_category_budget(user_key, matched_cat)
            await status_msg.edit_text(f"🗑️ تم إلغاء ميزانية تصنيف **{matched_cat}** بنجاح!", parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            if cb:
                buttons = [
                    [InlineKeyboardButton(f"🗑️ إلغاء ميزانية {k}", callback_data=f"delcatb:{k}")]
                    for k in cb.keys()
                ]
                buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="action:balance")])
                await status_msg.edit_text("🔍 **اختر ميزانية التصنيف التي تريد إلغاءها:**", reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await status_msg.edit_text("ℹ️ ليس لديك أي ميزانيات مخصصة للتصنيفات لإلغائها.", reply_markup=get_main_keyboard())

    elif intent == "query_monthly_closing":
        transactions = await fetch_all_transactions(user_key)
        report = generate_monthly_closing_report(user_key, transactions)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 رسم بياني للمصاريف", callback_data="chart_p:last_month:مصروف")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")]
        ])
        await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=keyboard)

    elif intent == "delete_transaction":
        transactions = await fetch_all_transactions(user_key)
        if not transactions:
            await status_msg.edit_text("⚠️ لا توجد أي معاملات مسجلة في شيت جوجل لمسحها!", reply_markup=get_main_keyboard())
            return

        amount = float(data.get("amount", 0))
        keyword = data.get("description", "") or data.get("category", "")
        is_last = data.get("is_last", False)

        target = find_matching_transaction(transactions, amount=amount, keyword=keyword, is_last=is_last)

        if not target:
            last_3 = list(reversed(transactions))[:3]
            hint_lines = "\n".join([f"• `{t['date']}` | {t['description']}: `{t['amount']:,.2f}` ج.م" for t in last_3])
            fail_msg = (
                f"🔍 **لم أجد معاملة مطابقة لمسحها!**\n"
                f"تأكد من المبلغ أو الكلمة.\n\n"
                f"📋 **آخر المعاملات المسجلة لديك:**\n{hint_lines}\n\n"
                f"💡 *يمكنك دائماً كتابة: 'احذف آخر عملية' لمسح أحدث معاملة فوراً.*"
            )
            await status_msg.edit_text(fail_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
            return

        success = await delete_from_sheet(user_key, [target["tx_id"]])
        if success:
            remaining_txs = [t for t in transactions if t["tx_id"] != target["tx_id"]]
            balances = calculate_wallet_balance(remaining_txs)
            badge = "💳" if target["payment_method"] == "فيزا" else ("🏦" if target["payment_method"] == "حصالة" else "💵")

            del_reply = (
                f"🗑️ **تم مسح المعاملة بنجاح من Google Sheets!**\n\n"
                f"📝 **الوصف:** {target['description']}\n"
                f"💰 **المبلغ:** `{target['amount']:,.2f}` ج.م ({target['type']})\n"
                f"📂 **التصنيف:** {target['category']}\n"
                f"{badge} **المحفظة:** {target['payment_method']}\n"
                f"📅 **التاريخ:** `{target['date']}`\n"
                f"🔖 **معرف المعاملة:** `{target['tx_id']}`\n"
                "─────────────────────\n"
                f"💼 **محفظتك بعد الحذف:**\n"
                f"💵 نقدي: `{balances['cash']:,.2f}` ج.م | 💳 فيزا: `{balances['visa']:,.2f}` ج.م\n"
                f"🏦 حصالة: `{balances['savings']:,.2f}` ج.م\n"
                f"💰 الإجمالي: `{balances['total']:,.2f}` ج.م"
            )
            await status_msg.edit_text(del_reply, parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            await status_msg.edit_text("❌ حدث خطأ أثناء تعديل حالة المعاملة في Google Sheets.")

    elif intent == "query_advice":
        await status_msg.edit_text("جاري استدعاء المستشار المالي... 🧠💡")
        transactions = await fetch_all_transactions(user_key)
        advice = await get_financial_advice(user_key, transactions)
        await status_msg.edit_text(advice, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif intent == "set_goal":
        goal_title = data.get("goal_title", "هدف ادخار")
        amount = float(data.get("amount", 0))
        if amount > 0:
            set_savings_goal(user_key, goal_title, amount)
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            await status_msg.edit_text(
                f"🎯 **تم تحديد هدف الادخار بنجاح!**\n\n" + format_savings_status(user_key, balances),
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )
        else:
            await status_msg.edit_text("⚠️ يرجى تحديد اسم الهدف والمبلغ بوضوح (مثال: 'عايز أحوش 15000 للابتوب').")

    elif intent == "query_goal":
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await status_msg.edit_text(format_savings_status(user_key, balances), parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif intent == "query_income":
        source = data.get("category", "all")
        time_period = data.get("time_period", "this_month")
        transactions = await fetch_all_transactions(user_key)
        report = generate_income_report(transactions, source=source, time_period=time_period)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📅 الشهر الحالي", callback_data=f"income_p:this_month:{source}"),
                    InlineKeyboardButton("⏳ آخر 7 أيام", callback_data=f"income_p:last_week:{source}"),
                ],
                [
                    InlineKeyboardButton("🗓️ إجمالي الدخل الكلي", callback_data=f"income_p:all_time:{source}"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=keyboard)

    elif intent == "query_excel":
        await status_msg.edit_text("جاري استخراج كشف الحساب وتجهيز ملف الإكسيل... 📊⏳")
        await send_excel_to_user(update.effective_chat.id, user_key, context)
        try:
            await status_msg.delete()
        except Exception:
            pass

    elif intent == "set_budget":
        amount = float(data.get("amount", 0))
        if amount > 0:
            prev_b = get_monthly_budget(user_key)
            set_monthly_budget(user_key, amount)
            log_user_action(user_key, {
                "action_type": "budget",
                "prev_amount": prev_b,
                "new_amount": amount,
                "summary": f"تحديد الميزانية الشهرية بمبلغ {amount:,.2f} ج.م",
            })
            await status_msg.edit_text(
                f"🎯 **تم تحديد ميزانيتك الشهرية بنجاح:** `{amount:,.2f}` ج.م",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )
        else:
            await status_msg.edit_text("⚠️ يرجى تحديد مبلغ صحيح للميزانية.")

    elif intent == "query_budget":
        transactions = await fetch_all_transactions(user_key)
        msg = format_budget_status(user_key, transactions)
        await status_msg.edit_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif intent == "query_chart":
        time_period = data.get("time_period", "this_month")
        category = data.get("category", "all")
        chart_target = data.get("chart_target", "مصروف")
        await status_msg.edit_text("جاري رسم المخطط البياني وتجهيز الصورة... 🎨⏳")
        await send_chart_to_user(update.effective_chat.id, user_key, context, chart_target=chart_target, category=category, time_period=time_period)
        try:
            await status_msg.delete()
        except Exception:
            pass

    elif intent == "query_balance":
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await status_msg.edit_text(
            format_wallet_message(user_key, balances),
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )

    elif intent == "query_report":
        category = data.get("category", "all")
        time_period = data.get("time_period", "this_month")
        transactions = await fetch_all_transactions(user_key)
        if time_period == "today":
            report = generate_daily_report(transactions)
        else:
            report = generate_report_by_criteria(transactions, category=category, time_period=time_period)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("☀️ اليوم", callback_data="period:today:all"),
                    InlineKeyboardButton("📅 الشهر الحالي", callback_data="period:this_month:all"),
                ],
                [
                    InlineKeyboardButton("⏳ آخر 7 أيام", callback_data="period:last_week:all"),
                    InlineKeyboardButton("🗓️ كل المصاريف", callback_data="period:all_time:all"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=keyboard)

    elif intent == "query_yearly":
        transactions = await fetch_all_transactions(user_key)
        year = data.get("year")
        report = generate_yearly_report(transactions, year=year)
        await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif intent == "query_compare":
        transactions = await fetch_all_transactions(user_key)
        month1 = data.get("month1")
        month2 = data.get("month2")
        report = generate_monthly_comparison(transactions, month1=month1, month2=month2)
        await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=get_main_keyboard())

    else:
        await status_msg.edit_text(
            "لم أفهم طلبك بوضوح.\nيمكنك كتابة عمليات متعددة معاً مثل:\nاستلفت من أحمد 500\nحول من الحصالة للفيزا 1000\nصرفت 50 غدا",
            reply_markup=get_main_keyboard()
        )


# ==========================================
# 4. معالجات الرسائل النصية والصوتية والصور
# ==========================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.effective_message.reply_text("⛔ عذراً، هذا البوت مخصص للمصرح لهم فقط.")
        return

    if not check_rate_limit(update.effective_user.id):
        await update.effective_message.reply_text("⚠️ أنت ترسل رسائل بسرعة كبيرة! استنى دقيقة وحاول تاني.")
        return

    user = update.effective_user
    user_key = get_user_identifier(user)
    register_active_chat(user_key, update.effective_chat.id)
    user_text = update.effective_message.text.strip()

    # فحص قفل PIN إذا كان مفعلاً
    user_pin = get_user_pin(user_key)
    if user_pin and not context.user_data.get("pin_unlocked"):
        if user_text == user_pin:
            context.user_data["pin_unlocked"] = True
            await update.effective_message.reply_text("🔓 **تم فتح القفل بنجاح! أهلاً بك.**", parse_mode="Markdown", reply_markup=get_main_keyboard())
            return
        else:
            await update.effective_message.reply_text("🔒 **البوت مقفل برمز PIN.**\nأرسل رمز الـ PIN لفتح البوت ومتابعة حساباتك:")
            return

    # مسار تعديل المبلغ (Edit Amount Flow)
    if context.user_data.get("editing_tx"):
        tx_id = context.user_data.pop("editing_tx")
        clean_num = re.sub(r"[^\d.]", "", user_text)
        if clean_num:
            new_amt = float(clean_num)
            await update_tx_amount(user_key, tx_id, new_amt)
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            await update.effective_message.reply_text(
                f"✅ **تم تعديل المبلغ إلى:** `{new_amt:,.2f}` ج.م بنجاح!\n\n" + format_wallet_message(user_key, balances),
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )
            return

    fast_del = fast_parse_deletion(user_text)
    if fast_del:
        if fast_del.get("is_last"):
            status = await update.effective_message.reply_text("جاري إلغاء وحذف آخر إجراء... ⚡🗑️")
            del_msg = await execute_undo_last_action(user_key)
            await status.edit_text(del_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
            return
        status = await update.effective_message.reply_text("جاري مسح المعاملة فوراً... ⚡🗑️")
        await process_user_input(update, context, fast_del, status)
        return

    status = await update.effective_message.reply_text("جاري المعالجة والتنفيذ... ⏳")
    data = await analyze_user_request(user_text, is_audio=False, user_key=user_key)
    await process_user_input(update, context, data, status)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not check_rate_limit(update.effective_user.id):
        await update.effective_message.reply_text("⚠️ استنى دقيقة قبل إرسال رسائل جديدة.")
        return

    user = update.effective_user
    user_key = get_user_identifier(user)
    user_pin = get_user_pin(user_key)
    if user_pin and not context.user_data.get("pin_unlocked"):
        await update.effective_message.reply_text("🔒 البوت مقفل. يرجى إدخال رمز الـ PIN نصياً أولاً.")
        return

    status = await update.effective_message.reply_text("جاري الاستماع للرسالة وتحليلها... 🎙️⚡")
    try:
        voice = update.effective_message.voice or update.effective_message.audio
        voice_file = await context.bot.get_file(voice.file_id)
        voice_bytes = await voice_file.download_as_bytearray()
        mime_type = voice.mime_type if hasattr(voice, "mime_type") and voice.mime_type else "audio/ogg"
        data = await analyze_user_request(bytes(voice_bytes), is_audio=True, mime_type=mime_type, user_key=user_key)
        await process_user_input(update, context, data, status)
    except Exception as e:
        await status.edit_text(f"خطأ في معالجة الصوت: {e}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not check_rate_limit(update.effective_user.id):
        await update.effective_message.reply_text("⚠️ استنى دقيقة قبل إرسال رسائل جديدة.")
        return

    user = update.effective_user
    user_key = get_user_identifier(user)
    user_pin = get_user_pin(user_key)
    if user_pin and not context.user_data.get("pin_unlocked"):
        await update.effective_message.reply_text("🔒 البوت مقفل. يرجى إدخال رمز الـ PIN نصياً أولاً.")
        return

    status = await update.effective_message.reply_text("جاري فحص الفاتورة وحفظ صورتها بالذكاء الاصطناعي... 📸🔍")
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        filename = f"receipt_{user_key}_{int(time.time())}.jpg"

        ai_task = analyze_receipt_image(bytes(photo_bytes))
        upload_task = async_upload_receipt_image(bytes(photo_bytes), filename)
        data, image_url = await asyncio.gather(ai_task, upload_task)

        if image_url:
            data["image_url"] = image_url
            for act in data.get("actions", []):
                act["image_url"] = image_url
            for tx in data.get("transactions", []):
                tx["image_url"] = image_url

        await process_user_input(update, context, data, status)
    except Exception as e:
        await status.edit_text(f"❌ خطأ أثناء معالجة صورة الفاتورة: {e}")


# ==========================================
# 5. معالجة الأزرار التفاعلية (Callback Query Handler)
# ==========================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        pass

    user_key = get_user_identifier(user)
    cb_data = query.data
    chat_id = update.effective_chat.id

    if cb_data == "action:cancel":
        context.user_data.clear()
        await edit_or_send(query, "✨ تم إلغاء العملية.")

    elif cb_data == "action:advice":
        await edit_or_send(query, "جاري استدعاء المستشار المالي... 🧠⏳")
        transactions = await fetch_all_transactions(user_key)
        advice = await get_financial_advice(user_key, transactions)
        await edit_or_send(query, advice, reply_markup=get_main_keyboard())

    elif cb_data == "action:debts":
        debts = await fetch_debts(user_key)
        await edit_or_send(query, format_debts_msg(debts), reply_markup=get_nav_keyboard())

    elif cb_data == "action:subs":
        subs = await fetch_subscriptions(user_key)
        buttons = []
        for s in subs:
            buttons.append([InlineKeyboardButton(f"🗑️ إلغاء اشتراك {s['name']}", callback_data=f"delsub:{s['id']}")])
        buttons.append([InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data="action:balance")])
        await edit_or_send(query, format_subs_msg(subs), reply_markup=InlineKeyboardMarkup(buttons))

    elif cb_data.startswith("switch:"):
        _, tx_id, target_w = cb_data.split(":")
        await update_tx_wallet(user_key, tx_id, target_w)
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        new_target = "فيزا" if target_w == "نقدي" else "نقدي"
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🔄 تحويل لـ {new_target}", callback_data=f"switch:{tx_id}:{new_target}"),
                InlineKeyboardButton("✏️ تعديل المبلغ", callback_data=f"editamt:{tx_id}"),
            ],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")]
        ])
        await edit_or_send(query, f"✅ **تم تحويل المحفظة إلى ({target_w}) بنجاح!**\n\n" + format_wallet_message(user_key, balances), reply_markup=kb)

    elif cb_data.startswith("switch_all:"):
        _, ids_str, target_w = cb_data.split(":")
        for t_id in ids_str.split(","):
            await update_tx_wallet(user_key, t_id, target_w)
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await edit_or_send(query, f"✅ **تم تحويل كل العمليات إلى ({target_w}) بنجاح!**\n\n" + format_wallet_message(user_key, balances), reply_markup=get_main_keyboard())

    elif cb_data.startswith("editamt:"):
        tx_id = cb_data.split(":")[1]
        context.user_data["editing_tx"] = tx_id
        await edit_or_send(query, "✏️ **أرسل المبلغ الجديد الآن بالأرقام:**", reply_markup=get_nav_keyboard())

    elif cb_data.startswith("undo_tx:"):
        tx_id = cb_data.split(":")[1]
        await delete_from_sheet(user_key, [tx_id])
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await edit_or_send(query, "🗑️ **تم التراجع عن المعاملة ومسحها بنجاح!**\n\n" + format_wallet_message(user_key, balances), reply_markup=get_main_keyboard())

    elif cb_data.startswith("undo_all:"):
        ids = cb_data.split(":")[1].split(",")
        await delete_from_sheet(user_key, ids)
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await edit_or_send(query, "🗑️ **تم التراجع عن جميع المعاملات ومسحها بنجاح!**\n\n" + format_wallet_message(user_key, balances), reply_markup=get_main_keyboard())

    elif cb_data.startswith("paysub:"):
        _, wallet, name, amt = cb_data.split(":")
        item = [{"description": f"اشتراك {name}", "amount": float(amt), "type": "مصروف", "category": "اشتراكات", "wallet": wallet}]
        await add_transactions(user_key, item)
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await edit_or_send(query, f"✅ **تم سداد اشتراك {name} بمبلغ `{float(amt):,.2f}` ج.م ({wallet}) بنجاح!**\n\n" + format_wallet_message(user_key, balances), reply_markup=get_main_keyboard())

    elif cb_data.startswith("undo:"):
        tx_ids = cb_data.split(":")[1].split(",")
        await delete_from_sheet(user_key, tx_ids)
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        undo_reply = (
            "🗑️ **تم إلغاء المعاملة وحذفها من Google Sheets بنجاح!**\n"
            f"🔖 كود المعاملة الملغاة: `{', '.join(tx_ids)}`\n\n"
            + format_wallet_message(user_key, balances)
        )
        await edit_or_send(query, undo_reply, reply_markup=get_main_keyboard())

    elif cb_data == "action:balance":
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        await edit_or_send(query, format_wallet_message(user_key, balances), reply_markup=get_main_keyboard())

    elif cb_data == "action:delete_last":
        del_reply = await execute_undo_last_action(user_key)
        await edit_or_send(query, del_reply, reply_markup=get_main_keyboard())


    elif cb_data == "action:export_excel":
        await context.bot.send_message(chat_id=chat_id, text="جاري استخراج كشف الحساب وتجهيز ملف الإكسيل... 📊⏳")
        await send_excel_to_user(chat_id, user_key, context)

    elif cb_data == "action:budget_info":
        transactions = await fetch_all_transactions(user_key)
        msg = format_budget_status(user_key, transactions)
        budget = get_monthly_budget(user_key)
        buttons = []
        if budget > 0:
            buttons.append([InlineKeyboardButton("🗑️ إلغاء الميزانية الشهرية", callback_data="action:delete_budget")])
        buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")])
        await edit_or_send(query, msg, reply_markup=InlineKeyboardMarkup(buttons))

    elif cb_data == "action:goal_info":
        transactions = await fetch_all_transactions(user_key)
        balances = calculate_wallet_balance(transactions)
        msg = format_savings_status(user_key, balances)
        curr_goal = get_savings_goal(user_key)
        buttons = []
        if curr_goal.get("target", 0) > 0:
            buttons.append([InlineKeyboardButton("🗑️ إلغاء هدف الادخار", callback_data="action:delete_goal")])
        buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")])
        await edit_or_send(query, msg, reply_markup=InlineKeyboardMarkup(buttons))

    elif cb_data == "action:delete_budget":
        prev_b = get_monthly_budget(user_key)
        if prev_b <= 0:
            await edit_or_send(query, "ℹ️ ليس لديك ميزانية شهرية محددة حالياً لإلغائها.", reply_markup=get_main_keyboard())
        else:
            set_monthly_budget(user_key, 0.0)
            log_user_action(user_key, {
                "action_type": "budget",
                "prev_amount": prev_b,
                "new_amount": 0.0,
                "summary": f"إلغاء الميزانية الشهرية ({prev_b:,.2f} ج.م)",
            })
            await edit_or_send(
                query,
                f"🗑️ **تم إلغاء الميزانية الشهرية بنجاح!**\n"
                f"الميزانية السابقة الملغاة: `{prev_b:,.2f}` ج.م\n\n"
                "💡 يمكنك تحديد ميزانية جديدة في أي وقت.",
                reply_markup=get_main_keyboard(),
            )

    elif cb_data == "action:delete_goal":
        curr_goal = get_savings_goal(user_key)
        if curr_goal.get("target", 0) <= 0:
            await edit_or_send(query, "ℹ️ ليس لديك هدف ادخار محدد حالياً لإلغائه.", reply_markup=get_main_keyboard())
        else:
            prev_title = curr_goal.get("title", "هدف الادخار")
            prev_target = float(curr_goal.get("target", 0.0))
            set_savings_goal(user_key, "تحويش عام", 0.0)
            log_user_action(user_key, {
                "action_type": "goal",
                "prev_goal": curr_goal,
                "new_goal": {"title": "تحويش عام", "target": 0.0},
                "summary": f"إلغاء هدف الادخار: {prev_title} ({prev_target:,.2f} ج.م)",
            })
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            await edit_or_send(
                query,
                f"🗑️ **تم إلغاء هدف الادخار بنجاح!**\n\n"
                f"🎯 الهدف الملغي: **{prev_title}** (`{prev_target:,.2f}` ج.م)\n"
                f"🏦 رصيدك في الحصالة لا زال محفوظاً: `{balances['savings']:,.2f}` ج.م\n\n"
                "💡 يمكنك تحديد هدف جديد في أي وقت.",
                reply_markup=get_main_keyboard(),
            )

    elif cb_data.startswith("delsub:"):
        sub_id = cb_data.split(":")[1]
        subs = await fetch_subscriptions(user_key)
        matched_sub = next((s for s in subs if s["id"] == sub_id), None)
        if matched_sub:
            await delete_subscription(user_key, sub_id)
            log_user_action(user_key, {
                "action_type": "subscription",
                "sub_id": sub_id,
                "name": matched_sub["name"],
                "amount": matched_sub["amount"],
                "summary": f"إلغاء اشتراك {matched_sub['name']}",
            })
            await edit_or_send(
                query,
                f"🗑️ تم إلغاء وحذف اشتراك **{matched_sub['name']}** بنجاح!",
                reply_markup=get_main_keyboard(),
            )
        else:
            await edit_or_send(query, "⚠️ لم يتم العثور على الاشتراك أو تم إلغاؤه مسبقاً.", reply_markup=get_main_keyboard())

    elif cb_data == "action:installments":
        insts = fetch_installments(user_key)
        msg = format_installments_msg(insts)
        buttons = []
        for i in [x for x in insts if x.get("status") == "نشط"]:
            buttons.append([
                InlineKeyboardButton(f"💳 سداد {i['name']} ({i['monthly_amount']:,.0f}ج)", callback_data=f"payinst:{i['id']}"),
                InlineKeyboardButton("🗑️ إلغاء", callback_data=f"delinst:{i['id']}")
            ])
        buttons.append([InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data="action:balance")])
        await edit_or_send(query, msg, reply_markup=InlineKeyboardMarkup(buttons))

    elif cb_data.startswith("payinst:"):
        inst_id = cb_data.split(":")[1]
        insts = fetch_installments(user_key)
        matched = next((i for i in insts if i.get("id") == inst_id and i.get("status") == "نشط"), None)
        if matched:
            res = pay_installment(user_key, inst_id)
            amt = float(matched["monthly_amount"])
            tx_item = [{
                "description": f"سداد قسط {matched['name']} ({res['paid_months']}/{res['total_months']})",
                "amount": amt,
                "type": "مصروف",
                "category": "أقساط والتزامات",
                "wallet": matched.get("wallet", "نقدي"),
            }]
            await add_transactions(user_key, tx_item)
            transactions = await fetch_all_transactions(user_key)
            balances = calculate_wallet_balance(transactions)
            rem_m = max(0, res['total_months'] - res['paid_months'])
            comp = "🎉 **(مبروك! تم سداد القسط بالكامل!)**" if res['status'] == 'مكتمل' else f"متبقي `{rem_m}` قسط."
            msg = (
                f"✅ **تم سداد قسط {matched['name']} بنجاح!**\n"
                f"💰 المبلغ المسدد: `{amt:,.2f}` ج.م ({matched.get('wallet', 'نقدي')})\n"
                f"📊 التقدم: `{res['paid_months']}` من `{res['total_months']}` قسط\n"
                f"{comp}\n\n"
                + format_wallet_message(user_key, balances)
            )
            await edit_or_send(query, msg, reply_markup=get_main_keyboard())
        else:
            await edit_or_send(query, "⚠️ القسط غير موجود أو تم سداده بالكامل مسبقاً.", reply_markup=get_main_keyboard())

    elif cb_data.startswith("delinst:"):
        inst_id = cb_data.split(":")[1]
        insts = fetch_installments(user_key)
        matched = next((i for i in insts if i.get("id") == inst_id), None)
        if matched:
            delete_installment(user_key, inst_id)
            log_user_action(user_key, {
                "action_type": "installment_delete",
                "inst_id": inst_id,
                "name": matched["name"],
                "summary": f"إلغاء قسط {matched['name']}",
            })
            await edit_or_send(query, f"🗑️ تم إلغاء وحذف قسط **{matched['name']}** بنجاح!", reply_markup=get_main_keyboard())
        else:
            await edit_or_send(query, "⚠️ لم يتم العثور على القسط المطلوب.", reply_markup=get_main_keyboard())

    elif cb_data.startswith("delcatb:"):
        cat = cb_data.split(":")[1]
        delete_category_budget(user_key, cat)
        await edit_or_send(query, f"🗑️ تم إلغاء ميزانية تصنيف **{cat}** بنجاح!", reply_markup=get_main_keyboard())

    elif cb_data == "action:monthly_closing":
        transactions = await fetch_all_transactions(user_key)
        report = generate_monthly_closing_report(user_key, transactions)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 رسم بياني للمصاريف", callback_data="chart_p:last_month:مصروف")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance")]
        ])
        await edit_or_send(query, report, reply_markup=keyboard)

    elif cb_data == "action:income_menu":
        msg = "💰 **اختر الفترة الزمنية لتقرير الدخل:**"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📅 الشهر الحالي", callback_data="income_p:this_month:all"),
                    InlineKeyboardButton("⏳ آخر 7 أيام", callback_data="income_p:last_week:all"),
                ],
                [
                    InlineKeyboardButton("🗓️ إجمالي الدخل الكلي", callback_data="income_p:all_time:all"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await edit_or_send(query, msg, reply_markup=keyboard)

    elif cb_data.startswith("income_p:"):
        _, period, source = cb_data.split(":")
        transactions = await fetch_all_transactions(user_key)
        report = generate_income_report(transactions, source=source, time_period=period)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📅 الشهر الحالي", callback_data=f"income_p:this_month:{source}"),
                    InlineKeyboardButton("⏳ آخر 7 أيام", callback_data=f"income_p:last_week:{source}"),
                ],
                [
                    InlineKeyboardButton("🗓️ إجمالي الدخل الكلي", callback_data=f"income_p:all_time:{source}"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await edit_or_send(query, report, reply_markup=keyboard)

    elif cb_data == "action:report_menu":
        msg = "📊 **اختر الفترة الزمنية لتقرير المصاريف:**"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("☀️ اليوم", callback_data="period:today:all"),
                    InlineKeyboardButton("📅 الشهر الحالي", callback_data="period:this_month:all"),
                ],
                [
                    InlineKeyboardButton("⏳ آخر 7 أيام", callback_data="period:last_week:all"),
                    InlineKeyboardButton("🗓️ كل المصاريف", callback_data="period:all_time:all"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await edit_or_send(query, msg, reply_markup=keyboard)

    elif cb_data.startswith("period:"):
        _, period, category = cb_data.split(":")
        transactions = await fetch_all_transactions(user_key)
        if period == "today":
            report = generate_daily_report(transactions)
        else:
            report = generate_report_by_criteria(transactions, category=category, time_period=period)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("☀️ اليوم", callback_data="period:today:all"),
                    InlineKeyboardButton("📅 الشهر الحالي", callback_data="period:this_month:all"),
                ],
                [
                    InlineKeyboardButton("⏳ آخر 7 أيام", callback_data="period:last_week:all"),
                    InlineKeyboardButton("🗓️ كل المصاريف", callback_data="period:all_time:all"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await edit_or_send(query, report, reply_markup=keyboard)

    elif cb_data == "action:chart_menu":
        msg = "📈 **اختر نوع الرسم البياني الذي تريده:**"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📉 مصاريف الشهر", callback_data="make_chart:مصروف:this_month:all"),
                    InlineKeyboardButton("💰 مصادر دخل الشهر", callback_data="make_chart:دخل:this_month:all"),
                ],
                [
                    InlineKeyboardButton("⚖️ مقارنة الدخل بالمصاريف", callback_data="make_chart:مقارنة:this_month:all"),
                ],
                [
                    InlineKeyboardButton("⏳ مصاريف آخر 7 أيام", callback_data="make_chart:مصروف:last_week:all"),
                    InlineKeyboardButton("🗓️ إجمالي الدخل الكلي", callback_data="make_chart:دخل:all_time:all"),
                ],
                [
                    InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="action:balance"),
                ],
            ]
        )
        await edit_or_send(query, msg, reply_markup=keyboard)

    elif cb_data.startswith("make_chart:"):
        parts = cb_data.split(":")
        chart_target = parts[1]
        period = parts[2]
        category = parts[3] if len(parts) > 3 else "all"
        await context.bot.send_message(chat_id=chat_id, text="جاري رسم المخطط البياني وتجهيز الصورة... 🎨⏳")
        await send_chart_to_user(chat_id, user_key, context, chart_target=chart_target, category=category, time_period=period)

    elif cb_data == "action:yearly":
        transactions = await fetch_all_transactions(user_key)
        report = generate_yearly_report(transactions)
        await edit_or_send(query, report, reply_markup=get_main_keyboard())

    elif cb_data == "action:compare":
        transactions = await fetch_all_transactions(user_key)
        report = generate_monthly_comparison(transactions)
        await edit_or_send(query, report, reply_markup=get_main_keyboard())

    elif cb_data == "action:split_help":
        msg = (
            "💱 **تقسيم الفواتير والحساب المشترك:**\n\n"
            "يمكنك تقسيم أي فاتورة بينك وبين أصدقائك بذكاء سواء بالتساوي أو بتحديد ما دفعه وما عليه كل شخص:\n\n"
            "✨ **1. بالطريقة الذكية (اكتب بالعامية في أي رسالة):**\n"
            "• `فاتورة كافيه 600 أنا دفعتها نقدي: أنا عليا 200 وأحمد 300 ومصطفى 100`\n"
            "• `اتغدينا أنا ومحمد وزياد بـ 1000، أنا دفعت 700 ومحمد دفع 300، وأنا عليا 200 ومحمد 300 وزياد 500`\n"
            "• `فاتورة 300 قسمها عليا أنا ومحمد بالتساوي`\n\n"
            "⚡ **2. بالأمر السريع:**\n"
            "• `/split 600 3`\n"
            "• `/split 600 أحمد مصطفى`"
        )
        await edit_or_send(query, msg, reply_markup=get_main_keyboard())
