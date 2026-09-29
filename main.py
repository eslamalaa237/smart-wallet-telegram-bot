import os
import sys
import threading
import logging
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config import TELEGRAM_BOT_TOKEN, CAIRO_TZ, validate_credentials
from sheets_db import setup_google_sheet
from handlers import (
    start,
    help_command,
    delete_last_command,
    advice_command,
    balance_command,
    today_command,
    report_command,
    chart_command,
    export_command,
    budget_command,
    set_budget_command,
    delete_budget_command,
    goal_command,
    set_goal_command,
    delete_goal_command,
    debts_command,
    subs_command,
    delete_sub_command,
    add_sub_command,
    split_command,
    refresh_command,
    yearly_command,
    compare_command,
    set_salary_command,
    handle_callback,
    handle_message,
    handle_voice,
    handle_photo,
    daily_evening_reminder,
    daily_subscription_check,
    auto_recurring_income_job,
    weekly_debt_reminder,
    installments_command,
    add_installment_command,
    pay_installment_command,
    delete_installment_command,
    set_cat_budget_command,
    delete_cat_budget_command,
    monthly_closing_command,
    monthly_closing_job,
    categories_command,
    add_category_command,
    remove_category_command,
    set_pin_command,
)


# ==========================================
# 1. خادم ويب موازي لإبقاء البوت مستيقظاً (Render 24/7)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Wallet Bot is running 24/7 on Cairo Time!")

    def log_message(self, format, *args):
        return


def run_web_server():
    port_str = os.environ.get("PORT")
    if not port_str:
        return
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logging.info(f"🌐 خادم الويب يعمل على المنفذ {port} للاستجابة لـ UptimeRobot.")
        server.serve_forever()
    except Exception as e:
        logging.warning(f"خادم الويب لم يبدأ (غير مطلوب على هذه البيئة): {e}")


# ==========================================
# 2. تسجيل قائمة الأوامر التلقائية في تليجرام
# ==========================================
async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("refresh", "🔄 تحديث الرصيد والقائمة"),
        BotCommand("delete_last", "🗑️ إلغاء أو حذف آخر عملية"),
        BotCommand("help", "📖 دليل الاستخدام الشامل"),
        BotCommand("balance", "💼 رصيد المحفظة"),
        BotCommand("today", "☀️ ملخص مصاريف اليوم"),
        BotCommand("debts", "📋 الديون والسلفيات"),
        BotCommand("subs", "🔁 الاشتراكات الشهرية"),
        BotCommand("installments", "💳 متابعة الأقساط والالتزامات"),
        BotCommand("monthly_closing", "🏆 تقرير تقفيل الشهر الختامي"),
        BotCommand("set_cat_budget", "🏷️ ميزانية تصنيف محدد"),
        BotCommand("add_sub", "➕ إضافة اشتراك دوري"),
        BotCommand("split", "💱 تقسيم فاتورة مع الأصدقاء"),
        BotCommand("report", "📊 تقارير المصاريف"),
        BotCommand("chart", "📈 الرسوم البيانية"),
        BotCommand("advice", "💡 مستشار الذكاء الاصطناعي"),
        BotCommand("export", "📁 تحميل كشف حساب Excel"),
        BotCommand("yearly", "📅 تقرير سنوي شامل"),
        BotCommand("compare", "⚖️ مقارنة شهرية"),
        BotCommand("set_salary", "💵 تسجيل مرتب تلقائي"),
        BotCommand("set_budget", "🎯 تحديد ميزانية"),
        BotCommand("set_goal", "🎯 هدف ادخار"),
        BotCommand("categories", "🏷️ استعراض التصنيفات"),
        BotCommand("add_category", "➕ إضافة تصنيف جديد"),
        BotCommand("set_pin", "🔐 قفل البوت بـ PIN"),
    ])
    print("✅ تم تسجيل قائمة الأوامر التلقائية في تليجرام بنجاح.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.warning(f"⚠️ تنبيه شبكة أو استجابة مؤقتة: {context.error}")


# ==========================================
# 3. تشغيل البوت (Main Entry Point)
# ==========================================
def main():
    if not validate_credentials():
        sys.exit(1)

    setup_google_sheet()

    threading.Thread(target=run_web_server, daemon=True).start()

    proxy_url = os.environ.get("https_proxy") or os.environ.get("http_proxy")
    req_kwargs = {
        "connect_timeout": 45.0,
        "read_timeout": 45.0,
        "write_timeout": 45.0,
        "pool_timeout": 45.0,
    }
    if proxy_url:
        req_kwargs["proxy"] = proxy_url
        logging.info(f"🌐 تفعيل البروكسي: {proxy_url}")

    t_request = HTTPXRequest(**req_kwargs)
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(t_request)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(error_handler)

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(daily_evening_reminder, time=dtime(hour=22, minute=0, tzinfo=CAIRO_TZ))
        job_queue.run_daily(daily_subscription_check, time=dtime(hour=9, minute=0, tzinfo=CAIRO_TZ))
        job_queue.run_daily(auto_recurring_income_job, time=dtime(hour=8, minute=0, tzinfo=CAIRO_TZ))
        job_queue.run_daily(weekly_debt_reminder, time=dtime(hour=12, minute=0, tzinfo=CAIRO_TZ), days=(4,))  # كل جمعة
        job_queue.run_daily(monthly_closing_job, time=dtime(hour=9, minute=0, tzinfo=CAIRO_TZ))
        print("⏰ تم تفعيل التنبيهات المجدولة (10م للاطمئنان، 9ص للاشتراكات، 8ص للمرتب، جمعة 12ظ للديون) بتوقيت مصر.")

    # تسجيل الأوامر
    app.add_handler(CommandHandler("refresh", refresh_command))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("delete_last", delete_last_command))
    app.add_handler(CommandHandler("advice", advice_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("budget", budget_command))
    app.add_handler(CommandHandler("set_budget", set_budget_command))
    app.add_handler(CommandHandler("delete_budget", delete_budget_command))
    app.add_handler(CommandHandler("goal", goal_command))
    app.add_handler(CommandHandler("set_goal", set_goal_command))
    app.add_handler(CommandHandler("delete_goal", delete_goal_command))
    app.add_handler(CommandHandler("debts", debts_command))
    app.add_handler(CommandHandler("subs", subs_command))
    app.add_handler(CommandHandler("delete_sub", delete_sub_command))
    app.add_handler(CommandHandler("add_sub", add_sub_command))
    app.add_handler(CommandHandler("split", split_command))
    app.add_handler(CommandHandler("yearly", yearly_command))
    app.add_handler(CommandHandler("compare", compare_command))
    app.add_handler(CommandHandler("set_salary", set_salary_command))
    app.add_handler(CommandHandler("installments", installments_command))
    app.add_handler(CommandHandler("add_installment", add_installment_command))
    app.add_handler(CommandHandler("pay_installment", pay_installment_command))
    app.add_handler(CommandHandler("delete_installment", delete_installment_command))
    app.add_handler(CommandHandler("set_cat_budget", set_cat_budget_command))
    app.add_handler(CommandHandler("delete_cat_budget", delete_cat_budget_command))
    app.add_handler(CommandHandler("monthly_closing", monthly_closing_command))
    app.add_handler(CommandHandler("categories", categories_command))
    app.add_handler(CommandHandler("add_category", add_category_command))
    app.add_handler(CommandHandler("remove_category", remove_category_command))
    app.add_handler(CommandHandler("set_pin", set_pin_command))

    # تسجيل معالجات التفاعل
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("🚀 البوت يعمل الآن بنجاح مع كامل الميزات والتسجيل الفوري والسلف والاشتراكات!")
    app.run_polling(bootstrap_retries=10)


if __name__ == "__main__":
    main()
