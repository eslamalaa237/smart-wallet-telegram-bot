# 💼 Your Wallet Bot (محفظتك الذكية) 🤖✨

بوت تليجرام مالي ذكي ومتكامل بالعامية المصرية، مدعوم بأحدث نماذج الذكاء الاصطناعي (**Google Gemini AI**) وقاعدة بيانات سحابية متزامنة عبر (**Google Sheets**).

---

## 🌟 أبرز الميزات (Features)

* 🎙️ **تسجيل صوتي ذكي (Voice Processing):** ابعت فويس بالعامية بأي عدد من العمليات، وهيتحلل ويتسجل فوراً في محفظتك.
* 📸 **مسح وقراءة الفواتير (Vision AI):** صوّر فاتورتك وسيقوم البوت بقراءتها، استخراج بنودها، وحفظ صورة الفاتورة تلقائياً.
* 📅 **تسجيل بتواريخ سابقة (Backdating):** نسيت تسجل مصروف قديم؟ قوله: `أول امبارح صرفت 150 غدا` وهيتسجل بالتاريخ الصحيح.
* 💳 **نظام متابعة الأقساط (Installments Tracking):**
  * إضافة أقساط شهرية مع تحديد المدة والمبلغ ومحفظة الدفع.
  * مؤشر بصري لنسبة السداد: `[🟩🟩🟩⬜⬜⬜] (3 من 6 شهور - 50%)`.
  * تذكير صباحي تلقائي يوم الاستحقاق مع زر سداد مباشر بنقرة واحدة.
* 🎯 **ميزانيات مخصصة لكل تصنيف (Category Budgets):**
  * حدد سقف إنفاق شهري لتصنيف معين (مثلاً: الأكل، المواصلات).
  * مؤشرات استهلاك وأشرطة تقدم تفصيلية لكل تصنيف.
  * تنبيهات ذكية عند الوصول إلى 85% و 100% من الميزانية.
* 🏆 **التقرير المالي الختامي للشهر (Monthly Closing):**
  * تقرير ختامي تلقائي يُرسل يوم 1 من كل شهر الساعة 9 صباحاً.
  * مقارنة تلقائية مع الشهر السابق، صافي التوفير، معدل الادخار، وأعلى 3 تصنيفات إنفاقاً.
* 🤝 **إدارة الديون والسلفيات بالمقاصة التلقائية:**
  * تسجيل ديون لك وعليك، مع حساب الصافي تلقائياً بين الأطراف المتداخلة.
* 💱 **تقسيم الفواتير مع الأصدقاء (Split Bill):**
  * تقسيم متساوٍ أو مخصص حسب كم دفع كل شخص وما هو نصيبه الفعلي، مع تسجيل الديون الناتجة تلقائياً.
* 📈 **تقارير ورسوم بيانية وكشوف Excel:**
  * رسوم بيانية تفاعلية عبر Matplotlib وتصدير كشف حساب كامل بصيغة Excel في ثوانٍ.
* 🔐 **قفل البوت برمز PIN سري:** لحماية خصوصية بياناتك المالية.
* 🌐 **دعم التشغيل السحابي 24/7:** مزود بخادم ويب مدمج (Healthcheck Server) للاستجابة لخدمات المراقبة مثل UptimeRobot على استضافات Render و Railway.

---

## 📁 الهيكلية المعمارية للمشروع (Project Structure)

```text
├── config.py              # الإعدادات، المفاتيح، توقيت القاهرة، وحماية Rate Limit
├── sheets_db.py           # طبقة قاعدة البيانات، Google Sheets، والكاش
├── ai_engine.py           # محرك Gemini AI (تحليل فويس، فواتير، واستخراج العمليات)
├── reports.py             # توليد التقارير المالية، الرسوم البيانية، وملفات Excel
├── keyboards.py           # لوحات المفاتيح والأزرار التفاعلية (Inline Keyboards)
├── handlers.py            # معالجات الأوامر والرسائل والتنبيهات المجدولة
├── main.py                # نقطة الانطلاق الرئيسية وتشغيل البوت وخادم الويب
├── Ur_wallet.py           # واجهة تشغيل مدمجة ومتوافقة بنسبة 100%
├── requirements.txt       # الحزم والمكتبات المطلوبة
├── .env.example           # نموذج لمتغيرات البيئة
└── credentials.json.example # نموذج لملف صلاحيات Google Cloud
```

---

## 🚀 طريقة التثبيت والتشغيل المحلي (Setup Guide)

### 1. استنساخ المشروع (Clone Repository)
```bash
git clone https://github.com/USERNAME/REPO_NAME.git
cd REPO_NAME
```

### 2. إنشاء بيئة عمل وتثبيت المتطلبات (Virtual Environment)
```bash
python -m venv venv
# لتفعيل البيئة على ويندوز:
venv\Scripts\activate
# لتفعيل البيئة على لينكس / ماك:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. إعداد المفاتيح والمتغيرات (Configuration)

1. أنشئ ملف `.env` بنسخ الملف النموذجي:
   ```bash
   cp .env.example .env
   ```
2. ضع مفاتيحك الخاصة داخل `.env`:
   * `TELEGRAM_BOT_TOKEN`: احصل عليه من بوت [@BotFather](https://t.me/botfather).
   * `GEMINI_API_KEY`: احصل عليه مجاناً من [Google AI Studio](https://aistudio.google.com/).
   * `SPREADSHEET_NAME`: اسم ملف Google Sheet على حسابك (الافتراضي: `Your_Wallet`).

### 4. إعداد ربط Google Sheets (Service Account)
1. من [Google Cloud Console](https://console.cloud.google.com/) قم بإنشاء **Service Account**.
2. فعّل مكتبتي **Google Sheets API** و **Google Drive API**.
3. قم بإنشاء مفتاح بصيغة `JSON` وحمّله وقم بتسميته `credentials.json` وضعه في المجلد الرئيسي للمشروع.
4. افتح شيت جوجل الخاص بك وقم بمشاركته مع الإيميل الخاص بالـ Service Account (بصلاحية `Editor`).

### 5. تشغيل البوت
يمكنك تشغيل البوت بأي من الأمرين:
```bash
python main.py
# أو
python Ur_wallet.py
```

---

## ☁️ النشر على السحابة (Cloud Deployment on Render)

المشروع جاهز ومُهيأ للنشر مجاناً على منصة **Render**:
1. اختر **Web Service** واربط مستودع GitHub الخاص بك.
2. اختر بيئة **Python 3**.
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `python main.py`
5. أضف متغيرات البيئة (`Environment Variables`):
   * `TELEGRAM_BOT_TOKEN`
   * `GEMINI_API_KEY`
   * `SPREADSHEET_NAME`
6. أضف محتوى `credentials.json` إما عبر Secret File أو تحميله.

---

## 🛡️ الأمان والخصوصية (Security)
* تم استبعاد ملفات المفاتيح الحساسة (`.env` و `credentials.json` و `user_config.json`) من مستودع Git لضمان عدم تسريب بياناتك المالية أو مفاتيح السحابة.

---

## 📜 الترخيص (License)
هذا المشروع مرخص تحت رخصة **MIT**. يمكنك استخدامه وتطويره بحرية.
