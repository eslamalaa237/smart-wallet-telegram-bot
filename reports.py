import io
import asyncio
from datetime import timedelta
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config import get_now
from sheets_db import (
    get_monthly_budget,
    get_category_budgets,
    get_savings_goal,
    normalize_ar_search,
    fetch_all_transactions,
    fetch_installments,
)
from keyboards import get_main_keyboard

def reshape_ar(text: str) -> str:
    """إعادة تشكيل النصوص العربية لتظهر من اليمين لليسار في Matplotlib بدون حروف مقلوبة."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    except ImportError:
        return str(text)


# ==========================================
# 1. الحسابات وأرصدة المحافظ
# ==========================================
def calculate_wallet_balance(transactions):
    cash = 0.0
    visa = 0.0
    savings = 0.0

    if not transactions:
        return {"cash": 0.0, "visa": 0.0, "savings": 0.0, "total": 0.0}

    for t in transactions:
        method = t.get("payment_method", "نقدي")
        amt = float(t.get("amount", 0.0))
        t_type = str(t.get("type", "")).strip()

        if "دخل" in t_type:
            if method == "فيزا":
                visa += amt
            elif method == "حصالة":
                savings += amt
            else:
                cash += amt
        elif "مصروف" in t_type:
            if method == "فيزا":
                visa -= amt
            elif method == "حصالة":
                savings -= amt
            else:
                cash -= amt

    return {"cash": cash, "visa": visa, "savings": savings, "total": cash + visa + savings}


def format_wallet_message(user_key: str, balances):
    budget = get_monthly_budget(user_key)
    budget_str = f"\n🎯 **الميزانية الشهرية:** `{budget:,.2f}` ج.م" if budget > 0 else ""

    goal = get_savings_goal(user_key)
    goal_str = ""
    if goal and goal.get("target", 0) > 0:
        target = goal["target"]
        curr = balances["savings"]
        pct = min(100.0, (curr / target) * 100) if target > 0 else 0
        goal_str = f"\n🎯 **هدف ({goal['title']}):** `{curr:,.0f}` / `{target:,.0f}` ج.م (`{pct:.1f}%`)"

    return (
        f"💼 **محفظتك المالية** (`{user_key}`):\n\n"
        f"💵 **الرصيد النقدي (كاش):** `{balances['cash']:,.2f}` ج.م\n"
        f"💳 **رصيد الفيزا / البنك:** `{balances['visa']:,.2f}` ج.م\n"
        f"🏦 **حصالة الادخار:** `{balances['savings']:,.2f}` ج.م\n"
        "─────────────────────\n"
        f"💰 **الإجمالي الكلي:** `{balances['total']:,.2f}` ج.م"
        + budget_str
        + goal_str
    )


def format_savings_status(user_key: str, balances) -> str:
    goal = get_savings_goal(user_key)
    target = goal.get("target", 0.0)
    curr = balances.get("savings", 0.0)
    title = goal.get("title", "تحويش عام")

    if target <= 0:
        return (
            "🎯 **حصالة الادخار:**\n\n"
            f"💰 **المبلغ المحوش حالياً:** `{curr:,.2f}` ج.م\n\n"
            "ℹ️ لم تقم بتحديد هدف مالي بعد.\n"
            "لتحديد هدف، اكتب مثلاً:\n"
            "`/set_goal شراء لابتوب 20000`"
        )

    pct = min(100.0, (curr / target) * 100) if target > 0 else 0
    rem = max(0.0, target - curr)
    bar_len = 10
    filled = int(min(pct, 100) // (100 / bar_len))
    bar = "█" * filled + "░" * (bar_len - filled)

    return (
        f"🎯 **هدف الادخار: {title}**\n\n"
        f"🏦 **المحوش في الحصالة:** `{curr:,.2f}` ج.م\n"
        f"🎯 **المبلغ المستهدف:** `{target:,.2f}` ج.م\n"
        f"📊 **نسبة الإنجاز:** `{bar}` `{pct:.1f}%`\n"
        f"⏳ **المتبقي للوصول للهدف:** `{rem:,.2f}` ج.م"
    )


def format_budget_status(user_key: str, transactions) -> str:
    budget = get_monthly_budget(user_key)
    cat_budgets = get_category_budgets(user_key)
    now = get_now()

    if budget <= 0 and not cat_budgets:
        return (
            "ℹ️ لم تقم بتحديد ميزانية شهرية بعد (الميزة اختيارية).\n\n"
            "🎯 **لتحديد ميزانية عامة:** اكتب مثلاً: `/set_budget 5000`\n"
            "🏷️ **لتحديد ميزانية لتصنيف:** اكتب مثلاً: `/set_cat_budget طعام ومشروبات 2000`"
        )

    start_date = now.strftime("%Y-%m-01")
    today_str = now.strftime("%Y-%m-%d")

    this_month_txs = [
        t for t in transactions
        if t["type"] == "مصروف" and t.get("category") != "تحويلات" and start_date <= t["date"] <= today_str
    ]
    month_expenses = sum(t["amount"] for t in this_month_txs)

    lines = [f"🎯 **موقف الميزانية لشهر {now.month}:**\n"]

    if budget > 0:
        percent = (month_expenses / budget) * 100
        rem = budget - month_expenses
        bar = "█" * int(min(percent, 100) // 10) + "░" * (10 - int(min(percent, 100) // 10))
        lines.append(
            f"💰 **الميزانية العامة:** `{budget:,.2f}` ج.م\n"
            f"💸 **ما تم صرفه حتى الآن:** `{month_expenses:,.2f}` ج.م ({percent:.1f}%)\n"
            f"📊 **مؤشر الاستهلاك:** `{bar}`\n"
            f"💵 **المتبقي للشهر:** `{rem:,.2f}` ج.م\n"
        )
    else:
        lines.append(f"💸 **إجمالي المصروفات الحالية للشهر:** `{month_expenses:,.2f}` ج.م\n")

    if cat_budgets:
        lines.append("─────────────────────\n🏷️ **ميزانيات التصنيفات المخصصة:**")
        cat_spending = {}
        for t in this_month_txs:
            c = t.get("category", "أخرى")
            cat_spending[c] = cat_spending.get(c, 0.0) + float(t.get("amount", 0.0))

        for cat, c_limit in cat_budgets.items():
            spent = cat_spending.get(cat, 0.0)
            c_pct = (spent / c_limit * 100) if c_limit > 0 else 0
            c_rem = c_limit - spent
            c_bar = "█" * int(min(c_pct, 100) // 10) + "░" * (10 - int(min(c_pct, 100) // 10))
            status_emoji = "🚨" if c_pct >= 100 else ("⚠️" if c_pct >= 85 else "✅")
            lines.append(
                f"\n{status_emoji} **{cat}:**\n"
                f"• المصروف: `{spent:,.2f}` / `{c_limit:,.2f}` ج.م (`{c_pct:.1f}%`)\n"
                f"• المؤشر: `{c_bar}`\n"
                f"• المتبقي: `{c_rem:,.2f}` ج.م"
            )

    return "\n".join(lines)


def check_budget_alert(user_key: str, transactions) -> str:
    alerts = []
    budget = get_monthly_budget(user_key)
    cat_budgets = get_category_budgets(user_key)

    now = get_now()
    start_date = now.strftime("%Y-%m-01")
    today_str = now.strftime("%Y-%m-%d")

    this_month_txs = [
        t for t in transactions
        if t["type"] == "مصروف" and t.get("category") != "تحويلات" and start_date <= t["date"] <= today_str
    ]

    if budget > 0:
        month_expenses = sum(t["amount"] for t in this_month_txs)
        percent = (month_expenses / budget) * 100
        rem = budget - month_expenses

        if percent >= 100:
            alerts.append(
                f"🚨 **تنبيه تجاوز الميزانية العامة:**\n"
                f"لقد تجاوزت ميزانيتك الشهرية (`{budget:,.0f}` ج.م) بنسبة `{percent:.1f}%`!\n"
                f"إجمالي مصروفات الشهر: `{month_expenses:,.2f}` ج.م (العجز: `{abs(rem):,.2f}` ج.م)."
            )
        elif percent >= 90:
            alerts.append(
                f"⚠️ **تحذير ميزانية (هام):**\n"
                f"استهلكت `{percent:.1f}%` من ميزانيتك العامة!\n"
                f"متبقي لك حتى نهاية الشهر: `{rem:,.2f}` ج.م فقط."
            )
        elif percent >= 70:
            alerts.append(
                f"💡 **تنبيه استهلاك الميزانية:**\n"
                f"وصلت إلى `{percent:.1f}%` من ميزانيتك العامة.\n"
                f"المتبقي لك: `{rem:,.2f}` ج.م."
            )

    if cat_budgets:
        cat_spending = {}
        for t in this_month_txs:
            c = t.get("category", "أخرى")
            cat_spending[c] = cat_spending.get(c, 0.0) + float(t.get("amount", 0.0))

        for cat, c_limit in cat_budgets.items():
            if c_limit <= 0:
                continue
            spent = cat_spending.get(cat, 0.0)
            c_pct = (spent / c_limit) * 100
            c_rem = c_limit - spent
            if c_pct >= 100:
                alerts.append(
                    f"🚨 **تنبيه تجاوز ميزانية ({cat}):**\n"
                    f"تجاوزت الحد المخصص (`{c_limit:,.0f}` ج.م) وصرفت `{spent:,.2f}` ج.م بنسبة `{c_pct:.1f}%`!"
                )
            elif c_pct >= 85:
                alerts.append(
                    f"⚠️ **تحذير ميزانية ({cat}):**\n"
                    f"استهلكت `{c_pct:.1f}%` من ميزانية {cat}، متبقي `{c_rem:,.2f}` ج.م."
                )

    if alerts:
        return "\n\n" + "\n\n".join(alerts)
    return ""


def format_installments_msg(installments: list) -> str:
    if not installments:
        return (
            "✨ **لا توجد أي أقساط مسجلة حالياً!**\n\n"
            "💡 لإضافة قسط جديد اكتب مثلاً:\n"
            "`/add_installment تكييف 750 10 5`\n"
            "*(الاسم ثم القسط الشهري ثم عدد الشهور ثم يوم الاستحقاق)*\n"
            "أو قل ببساطة:\n"
            "• *'عليا قسط تكييف 750 جنيه شهريا لمدة 10 شهور يوم 5'*"
        )

    active_insts = [i for i in installments if i.get("status") == "نشط"]
    completed_insts = [i for i in installments if i.get("status") == "مكتمل"]

    lines = ["💳 **جدول متابعة الأقساط والالتزامات:**\n"]

    if active_insts:
        total_monthly_commitment = sum(float(i.get("monthly_amount", 0)) for i in active_insts)
        total_remaining_balance = sum(
            float(i.get("monthly_amount", 0)) * max(0, int(i.get("total_months", 1)) - int(i.get("paid_months", 0)))
            for i in active_insts
        )

        lines.append(f"📌 **إجمالي الالتزام الشهري:** `{total_monthly_commitment:,.2f}` ج.م")
        lines.append(f"⏳ **إجمالي المبالغ المتبقية لجميع الأقساط:** `{total_remaining_balance:,.2f}` ج.م\n")
        lines.append("─────────────────────")

        for idx, inst in enumerate(active_insts, 1):
            name = inst.get("name", "قسط")
            amt = float(inst.get("monthly_amount", 0))
            total_m = int(inst.get("total_months", 1))
            paid_m = int(inst.get("paid_months", 0))
            rem_m = max(0, total_m - paid_m)
            rem_amt = amt * rem_m
            paid_amt = amt * paid_m
            day = inst.get("due_day", 1)
            pct = (paid_m / total_m * 100) if total_m > 0 else 0
            bar = "🟩" * int(min(pct, 100) // 10) + "⬜" * (10 - int(min(pct, 100) // 10))

            lines.append(
                f"\n{idx}. 📦 **{name}**\n"
                f"   • 💰 القسط الشهري: `{amt:,.2f}` ج.م ({inst.get('wallet', 'نقدي')})\n"
                f"   • 📅 يوم الاستحقاق: يوم `{day}` من كل شهر\n"
                f"   • 📊 التقدم: [{bar}] `{paid_m}` من `{total_m}` شهر (`{pct:.0f}%`)\n"
                f"   • 💵 المدفوع: `{paid_amt:,.2f}` ج.م | ⏳ المتبقي: `{rem_amt:,.2f}` ج.م ({rem_m} قسط)"
            )

    if completed_insts:
        lines.append("\n─────────────────────\n🎉 **أقساط مكتملة تم سدادها بالكامل:**")
        for c in completed_insts:
            tot = float(c.get("monthly_amount", 0)) * int(c.get("total_months", 1))
            lines.append(f"• ✅ **{c.get('name')}**: تم سداد `{tot:,.2f}` ج.م بالكامل")

    return "\n".join(lines)


def generate_monthly_closing_report(user_key: str, transactions: list, target_year: int = None, target_month: int = None) -> str:
    now = get_now()
    if target_year is None or target_month is None:
        first_of_this_month = now.replace(day=1)
        prev_month_last_day = first_of_this_month - timedelta(days=1)
        target_year = prev_month_last_day.year
        target_month = prev_month_last_day.month

    month_names = [
        "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
        "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"
    ]
    m_name = month_names[target_month] if 1 <= target_month <= 12 else str(target_month)

    start_date = f"{target_year:04d}-{target_month:02d}-01"
    if target_month == 12:
        next_month_start = f"{target_year + 1:04d}-01-01"
    else:
        next_month_start = f"{target_year:04d}-{target_month + 1:02d}-01"

    month_txs = [
        t for t in transactions
        if start_date <= t["date"] < next_month_start and t.get("category") != "تحويلات"
    ]

    income_txs = [t for t in month_txs if t["type"] == "دخل"]
    expense_txs = [t for t in month_txs if t["type"] == "مصروف"]

    total_income = sum(t["amount"] for t in income_txs)
    total_expense = sum(t["amount"] for t in expense_txs)
    net_savings = total_income - total_expense
    savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0

    cat_breakdown = {}
    for t in expense_txs:
        cat = t.get("category", "أخرى")
        cat_breakdown[cat] = cat_breakdown.get(cat, 0.0) + float(t["amount"])
    sorted_cats = sorted(cat_breakdown.items(), key=lambda x: x[1], reverse=True)

    prior_month = 12 if target_month == 1 else target_month - 1
    prior_year = target_year - 1 if target_month == 1 else target_year
    prior_start = f"{prior_year:04d}-{prior_month:02d}-01"
    prior_end = f"{target_year:04d}-{target_month:02d}-01"

    prior_txs = [
        t for t in transactions
        if prior_start <= t["date"] < prior_end and t.get("category") != "تحويلات" and t["type"] == "مصروف"
    ]
    prior_expense = sum(t["amount"] for t in prior_txs)

    diff_str = ""
    if prior_expense > 0:
        diff = total_expense - prior_expense
        diff_pct = (diff / prior_expense) * 100
        p_m_name = month_names[prior_month]
        if diff < 0:
            diff_str = f"📉 **المقارنة:** وفرت `{abs(diff):,.2f}` ج.م (`{abs(diff_pct):.1f}%`) مقارنة بشهر {p_m_name}! 👏🎉"
        else:
            diff_str = f"📈 **المقارنة:** زادت مصاريفك `{diff:,.2f}` ج.م (`{diff_pct:.1f}%`) عن شهر {p_m_name}."

    if net_savings > 0 and savings_rate >= 20:
        badge_rating = "🌟 **أداء مالي استثنائي!** (تم ادخار نسبة ممتازة من الدخل)"
    elif net_savings > 0:
        badge_rating = "👍 **أداء مالي إيجابي** (حققت فائضاً مالياً، استمر في ترشيد النفقات)"
    elif total_income == 0 and total_expense > 0:
        badge_rating = "⚠️ **تنبيه:** لم تسجل دخلاً خلال الشهر، فقط مصاريف."
    else:
        badge_rating = "⚠️ **تنبيه:** مصروفات الشهر تجاوزت الدخل (عجز مالي)."

    res = (
        f"🏆 **التقرير الختامي الشامل لشهر {m_name} {target_year}:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💵 **إجمالي الدخل الوارد:** `{total_income:,.2f}` ج.م\n"
        f"💸 **إجمالي المصروفات:** `{total_expense:,.2f}` ج.م\n"
        f"🏦 **صافي الفائض / المدخرات:** `{net_savings:,.2f}` ج.م "
        f"({'فائض' if net_savings >= 0 else 'عجز'}) ({savings_rate:.1f}%)\n"
    )
    if diff_str:
        res += f"{diff_str}\n"

    res += f"\n{badge_rating}\n\n"
    res += f"📊 **أعلى بنود استهلكت فلوسك في {m_name}:**\n"

    if sorted_cats:
        for idx, (cat, amt) in enumerate(sorted_cats[:3], 1):
            pct = (amt / total_expense * 100) if total_expense > 0 else 0
            res += f"{idx}. **{cat}:** `{amt:,.2f}` ج.م (`{pct:.1f}%`)\n"
    else:
        res += "• لا توجد مصاريف مسجلة في هذا الشهر.\n"

    budget = get_monthly_budget(user_key)
    if budget > 0:
        res += f"\n🎯 **التزامك بالميزانية العامة (`{budget:,.2f}` ج.م):** "
        if total_expense <= budget:
            res += f"✅ نجحت في الالتزام ووفرت `{budget - total_expense:,.2f}` ج.م!\n"
        else:
            res += f"❌ تجاوزت الميزانية بـ `{total_expense - budget:,.2f}` ج.م.\n"

    res += "━━━━━━━━━━━━━━━━━━━\n💡 *بداية شهر جديد موفقة ومباركة بإذن الله!*"
    return res


def format_debts_msg(debts: list, target_person: str = "") -> str:
    if not debts:
        return "✨ لا توجد أي ديون أو سلفيات مسجلة عليك أو لك حالياً!"

    norm_target = normalize_ar_search(target_person) if target_person else ""
    active_debts = [d for d in debts if d["status"] == "نشط" and d["remaining"] > 0]

    if norm_target:
        active_debts = [d for d in active_debts if norm_target in normalize_ar_search(d["person"])]
        if not active_debts:
            return f"✨ لا توجد أي ديون نشطة باسم **{target_person}** حالياً (الحساب مصفى بالكامل)!"

    people_summary = {}
    for d in active_debts:
        p_name = d["person"].strip()
        match_key = None
        for k in people_summary:
            if normalize_ar_search(k) == normalize_ar_search(p_name):
                match_key = k
                break
        if not match_key:
            match_key = p_name
            people_summary[match_key] = {"for_me": 0.0, "on_me": 0.0}

        if d["type"] == "ليا":
            people_summary[match_key]["for_me"] += float(d["remaining"])
        else:
            people_summary[match_key]["on_me"] += float(d["remaining"])

    on_me_lines = []
    for_me_lines = []
    tot_on = 0.0
    tot_for = 0.0

    for person, amounts in people_summary.items():
        net = amounts["for_me"] - amounts["on_me"]
        if net > 0:
            for_me_lines.append(f"  • **{person}**: لك عنده صافي `{net:,.2f}` ج.م")
            tot_for += net
        elif net < 0:
            abs_net = abs(net)
            on_me_lines.append(f"  • **{person}**: عليك له صافي `{abs_net:,.2f}` ج.م")
            tot_on += abs_net

    header = f"📋 **كشف حساب ديون ({target_person}):**\n\n" if target_person else "📋 **كشف حساب الديون (الصافي بعد التصفية والمقاصة):**\n\n"
    msg = header

    if on_me_lines:
        msg += f"🔴 **ديون عليك{' لـ ' + target_person if target_person else ' للآخرين'}:** `{tot_on:,.2f}` ج.م\n"
        msg += "\n".join(on_me_lines) + "\n\n"

    if for_me_lines:
        msg += f"🟢 **أموال لك{' عند ' + target_person if target_person else ' عند الآخرين'}:** `{tot_for:,.2f}` ج.م\n"
        msg += "\n".join(for_me_lines) + "\n\n"

    if not on_me_lines and not for_me_lines:
        return f"✨ الحساب متصفي تماماً مع **{target_person or 'الجميع'}**! (الصافي 0 ج.م)."

    msg += "💡 *الحساب مصفى تلقائياً (المقاصة مخصومة ومحسوبة بالصافي الفعلي).*"
    return msg.strip()


def format_subs_msg(subs: list) -> str:
    if not subs:
        return "ℹ️ لا توجد أي اشتراكات شهرية مسجلة.\nلإضافة اشتراك اكتب مثلاً:\n`/add_sub نت 450 1` (الاسم ثم المبلغ ثم يوم الشهر)."
    tot = sum(s["amount"] for s in subs)
    msg = f"🔁 **اشتراكاتك ومصاريفك الشهرية الثابتة:**\n💰 **الإجمالي الشهري:** `{tot:,.2f}` ج.م\n\n"
    for s in sorted(subs, key=lambda x: x["due_day"]):
        msg += f"• **{s['name']}**: `{s['amount']:,.2f}` ج.م (يوم {s['due_day']} شهرياً - {s['wallet']})\n"
    return msg


# ==========================================
# 2. تقارير النصوص والملخصات
# ==========================================
def generate_daily_report(transactions):
    if not transactions:
        return "قاعدة بياناتك فارغة، لم تسجل أي معاملات بعد!"

    today_str = get_now().strftime("%Y-%m-%d")

    today_expenses = [
        t for t in transactions
        if t["date"] == today_str and t["type"] == "مصروف" and t["category"] != "تحويلات"
    ]
    today_income = [
        t for t in transactions
        if t["date"] == today_str and t["type"] == "دخل" and t["category"] != "تحويلات"
    ]

    total_expense = sum(t["amount"] for t in today_expenses)
    total_income_amt = sum(t["amount"] for t in today_income)
    balances = calculate_wallet_balance(transactions)

    report = f"☀️ **تقرير مصاريف ودخل اليوم ({today_str}):**\n\n"
    report += f"💸 **إجمالي ما صرفته اليوم:** `{total_expense:,.2f}` ج.م\n"
    if total_income_amt > 0:
        report += f"💰 **إجمالي دخل اليوم:** `{total_income_amt:,.2f}` ج.م\n"

    report += "\n📋 **تفاصيل مصاريفك مقسمة بالبنود:**\n"
    report += "─────────────────────\n"

    if not today_expenses:
        report += "✨ لم تقم بصرف أي شيء اليوم حتى الآن!\n"
    else:
        categories_map = {}
        for t in today_expenses:
            cat = t["category"]
            if cat not in categories_map:
                categories_map[cat] = []
            categories_map[cat].append(t)

        for cat, items in categories_map.items():
            cat_total = sum(i["amount"] for i in items)
            report += f"\n📂 **{cat}** (المجموع: `{cat_total:,.2f}` ج.م):\n"
            for item in items:
                badge = "💳" if item["payment_method"] == "فيزا" else ("🏦" if item["payment_method"] == "حصالة" else "💵")
                report += f"  • {item['description']}: `{item['amount']:,.2f}` ج.م ({badge} {item['payment_method']})\n"

    report += "\n─────────────────────\n"
    report += "💼 **كشف حساب محفظتك المتبقي:**\n"
    report += f"💵 نقدي (كاش): `{balances['cash']:,.2f}` ج.م\n"
    report += f"💳 فيزا / بنك: `{balances['visa']:,.2f}` ج.م\n"
    report += f"🏦 حصالة الادخار: `{balances['savings']:,.2f}` ج.م\n"
    report += f"💰 **الإجمالي المتوفر لديك:** `{balances['total']:,.2f}` ج.م"
    return report


def generate_income_report(transactions, source="all", time_period="this_month"):
    if not transactions:
        return "قاعدة بياناتك فارغة، لم تسجل أي معاملات بعد!"

    now = get_now()
    today_str = now.strftime("%Y-%m-%d")

    if time_period == "today":
        start_date = today_str
        period_title = f"اليوم ({today_str})"
    elif time_period == "last_week":
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        period_title = "آخر 7 أيام"
    elif time_period == "all_time":
        start_date = "2000-01-01"
        period_title = "كل الوقت"
    else:
        start_date = now.strftime("%Y-%m-01")
        period_title = f"شهر {now.month} الحالي"

    filtered = [
        t for t in transactions
        if t["type"] == "دخل" and t.get("category") != "تحويلات" and start_date <= t["date"] <= today_str
    ]

    if source != "all":
        items = [
            t for t in filtered
            if source.lower() in t["category"].lower()
            or source.lower() in t["description"].lower()
            or t["category"].lower() in source.lower()
            or t["description"].lower() in source.lower()
        ]
        total_src = sum(i["amount"] for i in items)
        if not items:
            return f"🔍 لم يتم العثور على أي دخل مسجل من مصدر **({source})** في فترة ({period_title})."

        report = (
            f"💰 **تقرير الدخل من ({source}):**\n"
            f"🗓️ **الفترة:** {period_title}\n"
            f"💵 **إجمالي الدخل المحصل:** `{total_src:,.2f}` ج.م\n\n"
            f"📋 **العمليات المسجلة:**\n"
        )
        for i in sorted(items, key=lambda x: x["date"], reverse=True):
            badge = "💳" if i["payment_method"] == "فيزا" else ("🏦" if i["payment_method"] == "حصالة" else "💵")
            report += f"• `{i['date']}` | **{i['description']}**: `{i['amount']:,.2f}` ج.م ({badge} {i['payment_method']})\n"
        return report

    total_income = sum(t["amount"] for t in filtered)
    if not filtered:
        return f"📅 لم تسجل أي دخل في فترة ({period_title}) حتى الآن!"

    source_totals = {}
    for t in filtered:
        src = t["category"] if t["category"] not in ["عمل", "أخرى", "دخل"] else t["description"]
        source_totals[src] = source_totals.get(src, 0.0) + t["amount"]

    report = (
        f"📈 **تقرير إجمالي الدخل ({period_title}):**\n"
        f"🗓️ من `{start_date}` إلى `{today_str}`\n"
        f"💰 **إجمالي الدخل المحصل:** `{total_income:,.2f}` ج.م\n\n"
        f"📂 **توزيع مصادر الدخل:**\n"
    )
    for src, src_amt in sorted(source_totals.items(), key=lambda x: x[1], reverse=True):
        percent = (src_amt / total_income) * 100 if total_income > 0 else 0
        report += f"- **{src}**: `{src_amt:,.2f}` ج.م ({percent:.0f}%)\n"

    report += "\n📋 **آخر المعاملات المسجلة:**\n"
    for i in sorted(filtered, key=lambda x: x["date"], reverse=True)[:8]:
        badge = "💳" if i["payment_method"] == "فيزا" else ("🏦" if i["payment_method"] == "حصالة" else "💵")
        report += f"• `{i['date']}` | **{i['description']}**: `{i['amount']:,.2f}` ج.م ({badge} {i['payment_method']})\n"

    return report


def generate_report_by_criteria(transactions, category="all", time_period="this_month"):
    if not transactions:
        return "قاعدة بياناتك فارغة، لم تسجل أي معاملات بعد!"

    now = get_now()
    today_str = now.strftime("%Y-%m-%d")

    if time_period == "today":
        start_date = today_str
        period_title = f"اليوم ({today_str})"
    elif time_period == "last_week":
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        period_title = "آخر 7 أيام"
    elif time_period == "all_time":
        start_date = "2000-01-01"
        period_title = "كل الوقت"
    else:
        start_date = now.strftime("%Y-%m-01")
        period_title = f"شهر {now.month} الحالي"

    filtered = [
        t for t in transactions
        if t["type"] == "مصروف" and t.get("category") != "تحويلات" and start_date <= t["date"] <= today_str
    ]

    if category != "all":
        items = [
            t for t in filtered
            if category.lower() in t["category"].lower() or t["category"].lower() in category.lower()
        ]
        total_cat = sum(i["amount"] for i in items)
        if not items:
            return f"🔍 لا توجد مصاريف لبند **{category}** في فترة ({period_title})."

        report = f"🏷️ **تقرير مصاريف ({category}):**\n🗓️ **الفترة:** {period_title}\n💸 **إجمالي:** `{total_cat:,.2f}` ج.م\n\n📋 **العمليات:**\n"
        for i in sorted(items, key=lambda x: x["date"], reverse=True):
            badge = "💳" if i["payment_method"] == "فيزا" else ("🏦" if i["payment_method"] == "حصالة" else "💵")
            report += f"• `{i['date']}` | **{i['description']}**: `{i['amount']:,.2f}` ج.م ({badge} {i['payment_method']})\n"
        return report

    total_expense = sum(t["amount"] for t in filtered)
    if not filtered:
        return f"📅 لا توجد مصاريف مسجلة في ({period_title}) حتى الآن!"

    category_totals = {}
    for t in filtered:
        cat = t["category"]
        category_totals[cat] = category_totals.get(cat, 0.0) + t["amount"]

    report = (
        f"📊 **تقرير المصاريف الشامل ({period_title}):**\n"
        f"🗓️ من `{start_date}` إلى `{today_str}`\n"
        f"💸 **إجمالي المصروفات:** `{total_expense:,.2f}` ج.م\n\n"
        f"📂 **التوزيع على البنود:**\n"
    )
    for cat, cat_amt in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
        percent = (cat_amt / total_expense) * 100 if total_expense > 0 else 0
        report += f"- **{cat}**: {cat_amt:,.2f} ج.م ({percent:.0f}%)\n"
    return report


def generate_yearly_report(transactions, year=None):
    """تقرير سنوي شامل يوضح الدخل والمصاريف والبنود لكل شهر."""
    if not transactions:
        return "قاعدة بياناتك فارغة، لم تسجل أي معاملات بعد!"

    now = get_now()
    year = year or now.year
    year_str = str(year)

    year_txs = [t for t in transactions if t["date"].startswith(year_str) and t.get("category") != "تحويلات"]
    if not year_txs:
        return f"📅 لا توجد أي معاملات مسجلة لسنة {year}!"

    total_income = sum(t["amount"] for t in year_txs if t["type"] == "دخل")
    total_expense = sum(t["amount"] for t in year_txs if t["type"] == "مصروف")
    net = total_income - total_expense

    month_names = {1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
                   7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر"}

    monthly = {}
    for t in year_txs:
        month_num = int(t["date"][5:7])
        if month_num not in monthly:
            monthly[month_num] = {"income": 0, "expense": 0}
        if t["type"] == "دخل":
            monthly[month_num]["income"] += t["amount"]
        elif t["type"] == "مصروف":
            monthly[month_num]["expense"] += t["amount"]

    cat_totals = {}
    for t in year_txs:
        if t["type"] == "مصروف":
            cat_totals[t["category"]] = cat_totals.get(t["category"], 0) + t["amount"]
    top_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    month_savings = {m: d["income"] - d["expense"] for m, d in monthly.items()}
    best_month = max(month_savings, key=month_savings.get) if month_savings else None
    worst_month = min(month_savings, key=month_savings.get) if month_savings else None

    avg_monthly_expense = total_expense / len(monthly) if monthly else 0
    avg_monthly_income = total_income / len(monthly) if monthly else 0

    report = f"📊 **التقرير السنوي الشامل لسنة {year}:**\n\n"
    report += f"💰 **إجمالي الدخل:** `{total_income:,.2f}` ج.م\n"
    report += f"💸 **إجمالي المصاريف:** `{total_expense:,.2f}` ج.م\n"
    report += f"{'🟢' if net >= 0 else '🔴'} **الصافي:** `{net:,.2f}` ج.م\n"
    report += f"📅 **عدد الأشهر المسجلة:** {len(monthly)} شهر\n"
    report += f"📈 **متوسط الدخل الشهري:** `{avg_monthly_income:,.0f}` ج.م\n"
    report += f"📉 **متوسط المصاريف الشهرية:** `{avg_monthly_expense:,.0f}` ج.م\n"
    report += "\n─────────────────────\n"

    if best_month:
        report += f"🏆 **أفضل شهر ادخار:** {month_names.get(best_month, best_month)} (فائض `{month_savings[best_month]:,.0f}` ج.م)\n"
    if worst_month:
        w_val = month_savings[worst_month]
        report += f"📉 **أكثر شهر صرف:** {month_names.get(worst_month, worst_month)} ({'عجز' if w_val < 0 else 'فائض'} `{w_val:,.0f}` ج.م)\n"

    if top_cats:
        report += "\n📂 **أكبر 5 بنود مصاريف:**\n"
        for cat, amt in top_cats:
            pct = (amt / total_expense * 100) if total_expense > 0 else 0
            report += f"  • **{cat}**: `{amt:,.0f}` ج.م ({pct:.0f}%)\n"

    report += "\n📈 **ملخص شهري:**\n"
    for m in sorted(monthly.keys()):
        d = monthly[m]
        m_net = d["income"] - d["expense"]
        emoji = "🟢" if m_net >= 0 else "🔴"
        report += f"  {emoji} **{month_names.get(m, m)}**: دخل `{d['income']:,.0f}` | مصاريف `{d['expense']:,.0f}` | صافي `{m_net:,.0f}` ج.م\n"

    return report


def generate_monthly_comparison(transactions, month1=None, month2=None):
    """مقارنة مفصلة بين شهرين في الدخل والمصاريف والبنود."""
    if not transactions:
        return "قاعدة بياناتك فارغة!"

    now = get_now()
    if month2 is None:
        month2 = now.month
    if month1 is None:
        month1 = now.month - 1 if now.month > 1 else 12

    year = now.year
    year1 = year if month1 <= now.month else year - 1
    year2 = year

    month_names = {1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
                   7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر"}

    m1_prefix = f"{year1}-{month1:02d}"
    m2_prefix = f"{year2}-{month2:02d}"

    m1_txs = [t for t in transactions if t["date"].startswith(m1_prefix) and t.get("category") != "تحويلات"]
    m2_txs = [t for t in transactions if t["date"].startswith(m2_prefix) and t.get("category") != "تحويلات"]

    m1_income = sum(t["amount"] for t in m1_txs if t["type"] == "دخل")
    m1_expense = sum(t["amount"] for t in m1_txs if t["type"] == "مصروف")
    m2_income = sum(t["amount"] for t in m2_txs if t["type"] == "دخل")
    m2_expense = sum(t["amount"] for t in m2_txs if t["type"] == "مصروف")

    m1_name = month_names.get(month1, str(month1))
    m2_name = month_names.get(month2, str(month2))

    inc_diff = m2_income - m1_income
    exp_diff = m2_expense - m1_expense
    inc_pct = ((inc_diff / m1_income) * 100) if m1_income > 0 else 0
    exp_pct = ((exp_diff / m1_expense) * 100) if m1_expense > 0 else 0

    m1_cats = {}
    m2_cats = {}
    for t in m1_txs:
        if t["type"] == "مصروف":
            m1_cats[t["category"]] = m1_cats.get(t["category"], 0) + t["amount"]
    for t in m2_txs:
        if t["type"] == "مصروف":
            m2_cats[t["category"]] = m2_cats.get(t["category"], 0) + t["amount"]
    all_cats = set(list(m1_cats.keys()) + list(m2_cats.keys()))

    report = f"⚖️ **مقارنة شهرية: {m1_name} ↔ {m2_name}:**\n\n"
    report += f"📊 **{m1_name}:**\n"
    report += f"  💰 دخل: `{m1_income:,.0f}` ج.م | 💸 مصاريف: `{m1_expense:,.0f}` ج.م\n"
    report += f"  💵 صافي: `{m1_income - m1_expense:,.0f}` ج.م\n\n"
    report += f"📊 **{m2_name}:**\n"
    report += f"  💰 دخل: `{m2_income:,.0f}` ج.م | 💸 مصاريف: `{m2_expense:,.0f}` ج.م\n"
    report += f"  💵 صافي: `{m2_income - m2_expense:,.0f}` ج.م\n\n"
    report += "─────────────────────\n"
    report += f"📈 **الفرق في الدخل:** `{inc_diff:+,.0f}` ج.م ({inc_pct:+.1f}%)\n"
    report += f"📉 **الفرق في المصاريف:** `{exp_diff:+,.0f}` ج.م ({exp_pct:+.1f}%)\n\n"

    if all_cats:
        report += "📂 **مقارنة البنود:**\n"
        for cat in sorted(all_cats):
            v1 = m1_cats.get(cat, 0)
            v2 = m2_cats.get(cat, 0)
            diff = v2 - v1
            arrow = "🔺" if diff > 0 else ("🔻" if diff < 0 else "➖")
            report += f"  {arrow} **{cat}**: `{v1:,.0f}` → `{v2:,.0f}` (`{diff:+,.0f}`)\n"

    return report


# ==========================================
# 3. تصدير ملفات Excel
# ==========================================
def _generate_excel_sync(transactions):
    if not transactions:
        return None

    df = pd.DataFrame(transactions)
    col_map = {
        "date": "التاريخ",
        "description": "الوصف",
        "amount": "المبلغ (ج.م)",
        "type": "النوع",
        "category": "التصنيف",
        "payment_method": "المحفظة",
        "tx_id": "المعرف",
    }
    df = df.rename(columns=col_map)
    cols = ["التاريخ", "الوصف", "المبلغ (ج.م)", "النوع", "التصنيف", "المحفظة", "المعرف"]
    df = df[[c for c in cols if c in df.columns]]
    df = df.sort_values(by="التاريخ", ascending=False)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="كشف الحساب")
    buf.seek(0)
    return buf


async def generate_excel_file(transactions):
    return await asyncio.to_thread(_generate_excel_sync, transactions)


async def send_excel_to_user(chat_id, user_key: str, context):
    transactions = await fetch_all_transactions(user_key)
    excel_buf = await generate_excel_file(transactions)

    if excel_buf is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ لا توجد أي معاملات مسجلة لتصديرها إلى إكسيل!", reply_markup=get_main_keyboard())
    else:
        filename = f"كشف_حساب_{user_key}_{get_now().strftime('%Y_%m_%d')}.xlsx"
        await context.bot.send_document(
            chat_id=chat_id,
            document=excel_buf,
            filename=filename,
            caption=f"📊 **تم تصدير كشف حسابك** (`{user_key}`) **بنجاح إلى ملف إكسيل!**",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )


# ==========================================
# 4. الرسوم البيانية (Charts)
# ==========================================
def _generate_chart_sync(transactions, chart_target="مصروف", category="all", time_period="this_month"):
    if not transactions:
        return None, "قاعدة بياناتك فارغة، لا توجد معاملات لرسمها!"

    now = get_now()
    today_str = now.strftime("%Y-%m-%d")

    if time_period == "today":
        start_date = today_str
        period_title = f"اليوم ({today_str})"
    elif time_period == "last_week":
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        period_title = "آخر 7 أيام"
    elif time_period == "all_time":
        start_date = "2000-01-01"
        period_title = "كل الوقت"
    else:
        start_date = now.strftime("%Y-%m-01")
        period_title = f"شهر {now.month} الحالي"

    period_txs = [
        t for t in transactions
        if t.get("category") != "تحويلات" and start_date <= t["date"] <= today_str
    ]

    if chart_target == "مقارنة":
        total_inc = sum(t["amount"] for t in period_txs if t["type"] == "دخل")
        total_exp = sum(t["amount"] for t in period_txs if t["type"] == "مصروف")

        if total_inc == 0 and total_exp == 0:
            return None, f"📅 لا توجد معاملات مسجلة في ({period_title}) لإجراء مقارنة!"

        labels = [reshape_ar("إجمالي الدخل"), reshape_ar("إجمالي المصاريف")]
        values = [total_inc, total_exp]
        colors = ["#2ECC71", "#E74C3C"]

        fig, ax = plt.subplots(figsize=(7, 5))
        bars = ax.bar(labels, values, color=colors, width=0.45, edgecolor="#2C3E50", linewidth=1.2)

        for bar in bars:
            height = bar.get_height()
            ax.annotate(
                f"{height:,.0f} EGP",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center", va="bottom",
                fontsize=11, weight="bold", color="#2C3E50"
            )

        net_savings = total_inc - total_exp
        status_text = f"صافي الفائض: {net_savings:,.0f} EGP" if net_savings >= 0 else f"صافي العجز: {abs(net_savings):,.0f} EGP"
        ax.set_title(reshape_ar(f"مقارنة الدخل والمصاريف ({period_title})\n{status_text}"), fontsize=12, weight="bold", pad=15)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=180, bbox_inches="tight")
        buf.seek(0)
        plt.close(fig)

        caption = (
            f"⚖️ **مقارنة الدخل بالمصاريف ({period_title}):**\n\n"
            f"🟢 **إجمالي الدخل:** `{total_inc:,.2f}` ج.م\n"
            f"🔴 **إجمالي المصاريف:** `{total_exp:,.2f}` ج.م\n"
            f"💰 **الصافي:** `{net_savings:,.2f}` ج.م"
        )
        return buf, caption

    tx_type = "دخل" if chart_target == "دخل" else "مصروف"
    filtered = [t for t in period_txs if t["type"] == tx_type]

    if not filtered:
        return None, f"📅 لا توجد أي معاملات ({tx_type}) مسجلة في ({period_title}) لإنشاء رسم بياني!"

    if "," in category:
        selected_cats = [c.strip().lower() for c in category.split(",") if c.strip()]
        cat_sums = {}
        for c in selected_cats:
            amt = sum(t["amount"] for t in filtered if c in t["category"].lower() or t["category"].lower() in c or c in t["description"].lower())
            if amt > 0:
                cat_sums[c] = amt

        if not cat_sums:
            return None, f"🔍 لم يتم العثور على مبالغ مسجلة للبندين في فترة ({period_title})."

        fig, ax = plt.subplots(figsize=(7, 5))
        bars = ax.bar([reshape_ar(k) for k in cat_sums.keys()], list(cat_sums.values()), color="#3498DB", width=0.45)
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f"{height:,.0f} EGP", xy=(bar.get_x() + bar.get_width() / 2, height), xytext=(0, 4), textcoords="offset points", ha="center", va="bottom", fontsize=10, weight="bold")

        ax.set_title(reshape_ar(f"مقارنة بنود {tx_type} ({period_title})"), fontsize=12, weight="bold", pad=15)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=180, bbox_inches="tight")
        buf.seek(0)
        plt.close(fig)

        caption = f"📊 **مقارنة بنود ({category}) خلال {period_title}**"
        return buf, caption

    if category != "all":
        cat_items = [
            t for t in filtered
            if category.lower() in t["category"].lower()
            or category.lower() in t["description"].lower()
            or t["category"].lower() in category.lower()
            or t["description"].lower() in category.lower()
        ]
        if not cat_items:
            return None, f"🔍 لا توجد بيانات مسجلة لـ **{category}** في فترة ({period_title})!"

        daily_totals = {}
        for item in cat_items:
            date_key = item["date"][5:]
            daily_totals[date_key] = daily_totals.get(date_key, 0.0) + item["amount"]

        sorted_days = sorted(daily_totals.keys())
        daily_values = [daily_totals[d] for d in sorted_days]
        total_cat_amt = sum(daily_values)

        bar_color = "#27AE60" if tx_type == "دخل" else "#E67E22"
        fig, ax = plt.subplots(figsize=(8, 5))
        bars = ax.bar(sorted_days, daily_values, color=bar_color, width=0.5, edgecolor="#2C3E50", linewidth=1)

        for bar in bars:
            height = bar.get_height()
            ax.annotate(
                f"{height:,.0f}",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center", va="bottom",
                fontsize=9, weight="bold", color="#2C3E50"
            )

        ax.set_title(reshape_ar(f"مسار {tx_type} ({category}) خلال {period_title}"), fontsize=12, weight="bold", pad=15)
        ax.set_ylabel(reshape_ar("المبلغ (ج.م)"), fontsize=10, weight="bold")
        ax.set_xlabel(reshape_ar("التاريخ (شهر - يوم)"), fontsize=10, weight="bold")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        plt.xticks(rotation=40, ha="right", fontsize=9)

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=180, bbox_inches="tight")
        buf.seek(0)
        plt.close(fig)

        caption = (
            f"📈 **مسار {tx_type} ({category}):**\n"
            f"🗓️ **الفترة:** {period_title}\n"
            f"💰 **الإجمالي:** `{total_cat_amt:,.2f}` ج.م\n"
            f"📅 **عدد أيام التسجيل:** {len(sorted_days)} يوم"
        )
        return buf, caption

    category_totals = {}
    for t in filtered:
        cat = t["category"]
        if tx_type == "دخل" and cat in ["عمل", "أخرى", "دخل"]:
            cat = t["description"]
        category_totals[cat] = category_totals.get(cat, 0.0) + t["amount"]

    labels = list(category_totals.keys())
    values = list(category_totals.values())
    total_amt = sum(values)

    palette = (
        ["#2ECC71", "#1ABC9C", "#3498DB", "#9B59B6", "#F1C40F", "#E67E22"]
        if tx_type == "دخل"
        else ["#E74C3C", "#E67E22", "#F39C12", "#9B59B6", "#34495E", "#16A085"]
    )

    fig, ax = plt.subplots(figsize=(7, 6), subplot_kw=dict(aspect="equal"))
    reshaped_labels = [
        f"{reshape_ar(cat)} ({amt:,.0f} EGP)"
        for cat, amt in zip(labels, values)
    ]

    wedges, texts, autotexts = ax.pie(
        values,
        autopct="%1.1f%%",
        startangle=140,
        colors=palette[:len(labels)],
        pctdistance=0.78,
        wedgeprops=dict(width=0.45, edgecolor="white", linewidth=2),
    )

    for autotext in autotexts:
        autotext.set_color("black")
        autotext.set_fontsize(9)
        autotext.set_weight("bold")

    center_text = f"إجمالي ال{tx_type}\n{total_amt:,.0f}\nEGP"
    ax.text(0, 0, reshape_ar(center_text), ha="center", va="center", fontsize=11, weight="bold", color="#2C3E50")
    ax.set_title(reshape_ar(f"توزيع {tx_type} {period_title}"), fontsize=13, weight="bold", pad=20)
    ax.legend(wedges, reshaped_labels, title=reshape_ar("المصادر / البنود"), loc="center left", bbox_to_anchor=(0.95, 0.5), frameon=False)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=180, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)

    caption = (
        f"📊 **رسم بياني لتوزيع {tx_type}: {period_title}**\n"
        f"💰 **الإجمالي:** `{total_amt:,.2f}` ج.م"
    )
    return buf, caption


async def generate_unified_chart(transactions, chart_target="مصروف", category="all", time_period="this_month"):
    return await asyncio.to_thread(_generate_chart_sync, transactions, chart_target, category, time_period)


async def send_chart_to_user(chat_id, user_key: str, context, chart_target="مصروف", category="all", time_period="this_month"):
    transactions = await fetch_all_transactions(user_key)
    chart_buf, caption = await generate_unified_chart(
        transactions, chart_target=chart_target, category=category, time_period=time_period
    )

    if chart_buf is None:
        await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=get_main_keyboard())
    else:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=chart_buf,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
