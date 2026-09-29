from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ==========================================
# لوحات المفاتيح والأزرار التفاعلية
# ==========================================

def get_main_keyboard() -> InlineKeyboardMarkup:
    """لوحة المفاتيح التفاعلية الرئيسية للبوت."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("☀️ ملخص اليوم", callback_data="period:today:all"),
                InlineKeyboardButton("💡 مستشار AI", callback_data="action:advice"),
            ],
            [
                InlineKeyboardButton("📋 الديون والسلف", callback_data="action:debts"),
                InlineKeyboardButton("🔁 الاشتراكات", callback_data="action:subs"),
            ],
            [
                InlineKeyboardButton("💳 متابعة الأقساط", callback_data="action:installments"),
                InlineKeyboardButton("🏆 تقفيل الشهر", callback_data="action:monthly_closing"),
            ],
            [
                InlineKeyboardButton("🎯 حصالة الادخار", callback_data="action:goal_info"),
                InlineKeyboardButton("🎯 الميزانية", callback_data="action:budget_info"),
            ],
            [
                InlineKeyboardButton("📈 رسم بياني", callback_data="action:chart_menu"),
                InlineKeyboardButton("📁 كشف Excel", callback_data="action:export_excel"),
            ],
            [
                InlineKeyboardButton("💰 تقرير الدخل", callback_data="action:income_menu"),
                InlineKeyboardButton("📊 تقارير المصاريف", callback_data="action:report_menu"),
            ],
            [
                InlineKeyboardButton("📅 تقرير سنوي", callback_data="action:yearly"),
                InlineKeyboardButton("⚖️ مقارنة شهرية", callback_data="action:compare"),
            ],
            [
                InlineKeyboardButton("💱 تقسيم فاتورة", callback_data="action:split_help"),
                InlineKeyboardButton("🔄 تحديث الرصيد", callback_data="action:balance"),
            ],
            [
                InlineKeyboardButton("🗑️ إلغاء آخر عملية", callback_data="action:delete_last"),
                InlineKeyboardButton("❌ إغلاق", callback_data="action:cancel"),
            ],
        ]
    )


def get_nav_keyboard() -> InlineKeyboardMarkup:
    """زر الرجوع والإلغاء السريع."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 الرئيسية", callback_data="action:balance"),
            InlineKeyboardButton("❌ إلغاء", callback_data="action:cancel"),
        ]
    ])
