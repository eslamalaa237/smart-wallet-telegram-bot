import os
import re
import sys
import time
import logging
from collections import defaultdict
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

# ==========================================
# 1. التوقيت والإعدادات والمفاتيح
# ==========================================
CAIRO_TZ = ZoneInfo("Africa/Cairo")

def get_now() -> datetime:
    """الحصول على التوقيت الحالي المعتمد في مصر بدقة."""
    return datetime.now(CAIRO_TZ)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
CONFIG_FILE = os.path.join(BASE_DIR, "user_config.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")

# تحميل متغيرات البيئة من ملف .env محلياً إن وجد
if os.path.exists(ENV_FILE):
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Your_Wallet")

# 0 تعني متاح لأي مستخدم يفتح شات مع البوت
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))

# سلسلة الموديلات الاحتياطية لـ Gemini
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

# إعداد التسجيل (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ==========================================
# 2. Rate Limiting وحماية الكوتا
# ==========================================
_USER_RATE_LIMIT = defaultdict(list)
RATE_LIMIT_MAX = 20  # أقصى عدد رسائل في النافذة الزمنية
RATE_LIMIT_WINDOW = 60  # ثانية

def check_rate_limit(user_id: int) -> bool:
    """التحقق من عدم تجاوز المستخدم لحد الرسائل المسموح."""
    now_ts = time.time()
    _USER_RATE_LIMIT[user_id] = [t for t in _USER_RATE_LIMIT[user_id] if now_ts - t < RATE_LIMIT_WINDOW]
    if len(_USER_RATE_LIMIT[user_id]) >= RATE_LIMIT_MAX:
        return False
    _USER_RATE_LIMIT[user_id].append(now_ts)
    return True

# ==========================================
# 3. Retry Mechanism لإعادة المحاولة عند الضغط
# ==========================================
def with_retry(max_retries=3, delay=2, backoff=2):
    """Decorator لإعادة المحاولة تلقائياً عند حدوث أخطاء مؤقتة مع Google Sheets API."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    err_str = str(e).lower()
                    is_transient = any(w in err_str for w in ["quota", "rate", "429", "500", "503", "timeout", "timed out"])
                    if attempt < max_retries and is_transient:
                        logging.warning(f"⚠️ فشل {func.__name__} (محاولة {attempt}/{max_retries}): {e}. إعادة المحاولة بعد {current_delay}s...")
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        raise
        return wrapper
    return decorator

# ==========================================
# 4. دوال الفحص والتحقق والصلاحيات
# ==========================================
def get_user_identifier(user) -> str:
    """استخراج معرف إنجليزي آمن 100% لتجنب أخطاء تليجرام وGoogle Sheets."""
    if not user:
        return "user-unknown"
    if user.username:
        clean = re.sub(r"[^a-zA-Z0-9_]", "", user.username).strip()
        if clean:
            return clean.lower()
    return f"user-{user.id}"

def validate_credentials():
    """التحقق من وجود جميع المفاتيح وملف الصلاحيات قبل الإقلاع."""
    missing = []
    if not TELEGRAM_BOT_TOKEN.strip():
        missing.append("TELEGRAM_BOT_TOKEN (قم بوضعه في ملف .env أو كـ Environment Variable)")
    if not GEMINI_API_KEY.strip():
        missing.append("GEMINI_API_KEY (قم بوضعه في ملف .env أو كـ Environment Variable)")
    if not os.path.exists(CREDENTIALS_FILE):
        missing.append(f"ملف الصلاحيات ({CREDENTIALS_FILE}) غير موجود في مجلد المشروع! راجع credentials.json.example")

    if missing:
        print("\n" + "=" * 50)
        print("❌ تنبيه: هناك متطلبات ناقصة للتشغيل:")
        for item in missing:
            print(f"   - {item}")
        print("=" * 50 + "\n")
        return False
    return True

def is_authorized(user_id: int) -> bool:
    """التحقق من صلاحية المستخدم للوصول للبوت."""
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID

def normalize_wallet(name: str) -> str:
    """توحيد اسم المحفظة (نقدي، فيزا، حصالة)."""
    if not name:
        return "نقدي"
    name_str = str(name).lower()
    if any(w in name_str for w in ["حصالة", "تحويش", "ادخار", "saving", "savings"]):
        return "حصالة"
    if any(w in name_str for w in ["فيزا", "visa", "بنك", "bank", "كارت", "حساب", "فودافون"]):
        return "فيزا"
    return "نقدي"
