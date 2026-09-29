"""
Ur_wallet.py - Your Wallet Telegram Bot (Main Entry Point)
================================================================================
تم تقسيم وهيكلة المشروع بالكامل إلى وحدات معمارية متخصصة ومستقلة:
  - config.py      : الإعدادات، مفاتيح التشغيل، توقيت القاهرة ومحددات الأمان.
  - sheets_db.py   : قاعدة البيانات، التعامل مع Google Sheets، الكاش وإعدادات المستخدم.
  - ai_engine.py   : نماذج الذكاء الاصطناعي (Gemini)، تحليل الفويس، وقراءة الفواتير.
  - reports.py     : التقارير المالية، الرسوم البيانية (Matplotlib)، ملفات Excel، والأقساط.
  - keyboards.py   : لوحات المفاتيح والأزرار التفاعلية (Telegram Inline Keyboards).
  - handlers.py    : معالجات الأوامر والرسائل والتنبيهات المجدولة.
  - main.py        : تشغيل البوت وإدارة خادم الويب (Render 24/7).

يمكنك تشغيل البوت كالمعتاد عبر:
  python Ur_wallet.py   أو   python main.py
"""

from main import main

if __name__ == "__main__":
    main()
