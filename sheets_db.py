import asyncio
import json
import os
import re
import threading
import uuid
import gspread

from config import (
    BASE_DIR,
    CREDENTIALS_FILE,
    CONFIG_FILE,
    SPREADSHEET_NAME,
    get_now,
    normalize_wallet,
    with_retry,
)


# كاش عام للملف وصفحات المستخدمين
_CACHED_GC = None
_CACHED_SPREADSHEET = None
_CACHED_SHEETS = {}
_CONFIG_LOCK = threading.Lock()

def clear_sheets_cache():
    """مسح الكاش لإجبار إعادة الاتصال والقراءة من Google Sheets."""
    global _CACHED_GC, _CACHED_SPREADSHEET, _CACHED_SHEETS
    _CACHED_GC = None
    _CACHED_SPREADSHEET = None
    _CACHED_SHEETS.clear()


# ==========================================
# 1. إدارة الإعدادات المحلية (user_config.json)
# ==========================================
def _load_all_user_configs() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_all_user_configs(data: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _update_user_config(user_key: str, updater):
    with _CONFIG_LOCK:
        data = _load_all_user_configs()
        if user_key not in data:
            data[user_key] = {}
        updater(data[user_key])
        _save_all_user_configs(data)


def get_monthly_budget(user_key: str) -> float:
    data = _load_all_user_configs()
    return float(data.get(user_key, {}).get("monthly_budget", 0.0))


def set_monthly_budget(user_key: str, amount: float):
    _update_user_config(user_key, lambda cfg: cfg.update(monthly_budget=max(0.0, float(amount))))


def get_category_budgets(user_key: str) -> dict:
    data = _load_all_user_configs()
    return data.get(user_key, {}).get("category_budgets", {})


def set_category_budget(user_key: str, category: str, amount: float):
    def _updater(cfg):
        cb = dict(cfg.get("category_budgets", {}))
        if amount > 0:
            cb[category] = max(0.0, float(amount))
        else:
            cb.pop(category, None)
        cfg["category_budgets"] = cb
    _update_user_config(user_key, _updater)


def delete_category_budget(user_key: str, category: str):
    def _updater(cfg):
        cb = dict(cfg.get("category_budgets", {}))
        cb.pop(category, None)
        cfg["category_budgets"] = cb
    _update_user_config(user_key, _updater)


def fetch_installments(user_key: str) -> list:
    data = _load_all_user_configs()
    return data.get(user_key, {}).get("installments", [])


def add_installment(user_key: str, name: str, monthly_amount: float, total_months: int, due_day: int = 1, paid_months: int = 0, wallet: str = "نقدي") -> str:
    inst_id = f"INST-{uuid.uuid4().hex[:6].upper()}"
    new_inst = {
        "id": inst_id,
        "name": name,
        "monthly_amount": max(0.0, float(monthly_amount)),
        "total_months": max(1, int(total_months)),
        "paid_months": max(0, int(paid_months)),
        "due_day": max(1, min(31, int(due_day))),
        "wallet": normalize_wallet(wallet),
        "status": "نشط",
        "start_date": get_now().strftime("%Y-%m-%d"),
    }
    def _updater(cfg):
        insts = list(cfg.get("installments", []))
        insts.append(new_inst)
        cfg["installments"] = insts
    _update_user_config(user_key, _updater)
    return inst_id


def pay_installment(user_key: str, inst_id: str) -> dict:
    updated_info = None
    def _updater(cfg):
        nonlocal updated_info
        insts = list(cfg.get("installments", []))
        for inst in insts:
            if inst.get("id") == inst_id and inst.get("status") == "نشط":
                inst["paid_months"] = inst.get("paid_months", 0) + 1
                if inst["paid_months"] >= inst["total_months"]:
                    inst["status"] = "مكتمل"
                updated_info = dict(inst)
                break
        cfg["installments"] = insts
    _update_user_config(user_key, _updater)
    return updated_info


def delete_installment(user_key: str, inst_id: str) -> bool:
    deleted = False
    def _updater(cfg):
        nonlocal deleted
        insts = list(cfg.get("installments", []))
        before_len = len(insts)
        insts = [i for i in insts if i.get("id") != inst_id]
        if len(insts) < before_len:
            deleted = True
        cfg["installments"] = insts
    _update_user_config(user_key, _updater)
    return deleted


def get_savings_goal(user_key: str) -> dict:
    data = _load_all_user_configs()
    return data.get(user_key, {}).get("savings_goal", {"title": "تحويش عام", "target": 0.0})


def set_savings_goal(user_key: str, title: str, target: float):
    _update_user_config(
        user_key,
        lambda cfg: cfg.update(savings_goal={"title": title, "target": max(0.0, float(target))}),
    )


def register_active_chat(user_key: str, chat_id: int):
    _update_user_config(user_key, lambda cfg: cfg.update(chat_id=chat_id))


def get_all_active_users() -> dict:
    data = _load_all_user_configs()
    return {k: v.get("chat_id") for k, v in data.items() if v.get("chat_id")}


def get_recurring_income(user_key: str) -> dict:
    """الحصول على إعدادات الدخل المتكرر (المرتب) للمستخدم."""
    data = _load_all_user_configs()
    return data.get(user_key, {}).get("recurring_income", {})


def set_recurring_income(user_key: str, amount: float, description: str, wallet: str, day: int):
    """تسجيل دخل شهري متكرر (مرتب / إيراد ثابت)."""
    _update_user_config(user_key, lambda cfg: cfg.update(
        recurring_income={"amount": max(0, float(amount)), "description": description,
                         "wallet": wallet, "day": int(day)}
    ))


def log_user_action(user_key: str, action: dict):
    """تسجيل أي إجراء تم تنفيذه للمستخدم في سجل للتراجع عنه بدقة."""
    def _updater(cfg):
        history = cfg.get("action_history", [])
        history.append(action)
        cfg["action_history"] = history[-25:]
    _update_user_config(user_key, _updater)


def pop_last_user_action(user_key: str) -> dict:
    """استرجاع وحذف آخر إجراء تم من المستخدم للتراجع عنه."""
    result = {}
    def _updater(cfg):
        nonlocal result
        history = cfg.get("action_history", [])
        if history:
            result = history.pop()
            cfg["action_history"] = history
    _update_user_config(user_key, _updater)
    return result


def peek_last_user_action(user_key: str) -> dict:
    """استعراض آخر إجراء للمستخدم."""
    data = _load_all_user_configs()
    history = data.get(user_key, {}).get("action_history", [])
    return history[-1] if history else {}


# قائمة التصنيفات الافتراضية
DEFAULT_CATEGORIES = [
    "طعام ومشروبات", "مواصلات", "تسوق", "فواتير", "ترفيه",
    "صحة وعلاج", "تعليم", "سفر", "التزامات", "سداد ديون", "أخرى"
]


def get_user_categories(user_key: str) -> list:
    """الحصول على قائمة التصنيفات المعتمدة للمستخدم (الافتراضية + المخصصة)."""
    data = _load_all_user_configs()
    custom = data.get(user_key, {}).get("custom_categories", [])
    combined = list(DEFAULT_CATEGORIES)
    for c in custom:
        if c not in combined:
            combined.append(c)
    return combined


def add_user_category(user_key: str, category_name: str) -> bool:
    """إضافة تصنيف مخصص جديد للمستخدم."""
    clean_cat = category_name.strip()
    if not clean_cat:
        return False
    current = get_user_categories(user_key)
    if clean_cat in current:
        return False
    _update_user_config(
        user_key,
        lambda cfg: cfg.setdefault("custom_categories", []).append(clean_cat)
        if clean_cat not in cfg.get("custom_categories", []) else None
    )
    return True


def remove_user_category(user_key: str, category_name: str) -> bool:
    """حذف تصنيف مخصص."""
    clean_cat = category_name.strip()
    if clean_cat in DEFAULT_CATEGORIES:
        return False
    data = _load_all_user_configs()
    custom = data.get(user_key, {}).get("custom_categories", [])
    if clean_cat not in custom:
        return False
    _update_user_config(
        user_key,
        lambda cfg: cfg.update(custom_categories=[c for c in cfg.get("custom_categories", []) if c != clean_cat])
    )
    return True


def get_user_pin(user_key: str) -> str:
    """الحصول على رمز PIN لقفل البوت إذا كان مفعلاً."""
    data = _load_all_user_configs()
    return str(data.get(user_key, {}).get("pin_code", "")).strip()


def set_user_pin(user_key: str, pin: str):
    """تحديد أو إلغاء رمز PIN."""
    _update_user_config(user_key, lambda cfg: cfg.update(pin_code=str(pin).strip()))


# ==========================================
# 2. دوال الاتصال وفتح صفحات Google Sheets
# ==========================================
def get_spreadsheet():
    global _CACHED_GC, _CACHED_SPREADSHEET
    if _CACHED_SPREADSHEET is not None:
        return _CACHED_SPREADSHEET
    try:
        _CACHED_GC = gspread.service_account(filename=CREDENTIALS_FILE)
        _CACHED_SPREADSHEET = _CACHED_GC.open(SPREADSHEET_NAME)
        return _CACHED_SPREADSHEET
    except Exception as e:
        print(f"❌ خطأ فتح ملف Google Spreadsheet: {e}")
        return None


def get_or_create_sheet(sheet_title: str, headers: list, force_refresh=False):
    global _CACHED_SHEETS, _CACHED_SPREADSHEET
    if not force_refresh and sheet_title in _CACHED_SHEETS:
        return _CACHED_SHEETS[sheet_title]

    ss = get_spreadsheet()
    if ss is None:
        return None

    try:
        sheet = ss.worksheet(sheet_title)
    except gspread.WorksheetNotFound:
        try:
            sheet = ss.add_worksheet(title=sheet_title, rows=1000, cols=12)
            end_col = chr(65 + len(headers) - 1)
            sheet.update(range_name=f"A1:{end_col}1", values=[headers])
            print(f"✅ تم إنشاء ورقة عمل جديدة: {sheet_title}")
        except Exception as e:
            if "already exists" in str(e).lower():
                _CACHED_SPREADSHEET = None
                ss = get_spreadsheet()
                sheet = ss.worksheet(sheet_title)
            else:
                print(f"❌ خطأ إنشاء ورقة {sheet_title}: {e}")
                return None
    except Exception as e:
        print(f"❌ خطأ فتح ورقة {sheet_title}: {e}")
        return None

    _CACHED_SHEETS[sheet_title] = sheet
    return sheet


def upload_receipt_image(image_bytes: bytes, filename: str = "receipt.jpg") -> str:
    """رفع صورة الفاتورة إلى سيرفر استضافة صور دائم لحفظها وعرضها كصورة داخل Google Sheets."""
    if not image_bytes:
        return ""

    # حفظ نسخة محلية احتياطية في مجلد receipts
    try:
        receipts_dir = os.path.join(BASE_DIR, "receipts")
        os.makedirs(receipts_dir, exist_ok=True)
        local_path = os.path.join(receipts_dir, filename)
        with open(local_path, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        print(f"⚠️ فشل حفظ نسخة محلية من الفاتورة: {e}")

    # 1. الرفع الأساسي عبر Catbox (سريع ودائم ومباشر)
    try:
        import requests
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filename, image_bytes, "image/jpeg")},
            timeout=12,
        )
        if resp.status_code == 200 and resp.text.startswith("http"):
            return resp.text.strip()
    except Exception as e:
        print(f"⚠️ Catbox upload error: {e}")

    # 2. خطة بديلة احتياطية عبر FreeImage.host
    try:
        import base64
        import requests
        b64 = base64.b64encode(image_bytes).decode()
        resp = requests.post(
            "https://freeimage.host/api/1/upload",
            data={"key": "6d207e02198a847aa98d0a2a901485a5", "source": b64, "format": "json"},
            timeout=12,
        )
        if resp.status_code == 200:
            url = resp.json().get("image", {}).get("url")
            if url:
                return url
    except Exception as e:
        print(f"⚠️ Freeimage upload error: {e}")

    return ""


async def async_upload_receipt_image(image_bytes: bytes, filename: str = "receipt.jpg") -> str:
    return await asyncio.to_thread(upload_receipt_image, image_bytes, filename)


def get_user_sheet(user_key: str):
    headers = ["التاريخ", "الوصف", "المبلغ", "النوع", "التصنيف", "طريقة الدفع", "الحالة", "المعرف", "صورة الفاتورة"]
    sheet = get_or_create_sheet(user_key, headers)
    if sheet:
        try:
            r1 = sheet.row_values(1)
            if len(r1) < 9:
                sheet.update_cell(1, 9, "صورة الفاتورة")
        except Exception:
            pass
    return sheet


def get_debts_sheet(user_key: str):
    headers = ["المعرف", "التاريخ", "النوع", "اسم الشخص", "المبلغ الأصلي", "المسدد", "المتبقي", "الحالة"]
    return get_or_create_sheet(f"debts_{user_key}", headers)


def get_subs_sheet(user_key: str):
    headers = ["المعرف", "الاسم", "المبلغ", "يوم الاستحقاق", "المحفظة", "الحالة"]
    return get_or_create_sheet(f"subs_{user_key}", headers)


def setup_google_sheet():
    try:
        ss = get_spreadsheet()
        if ss:
            print(f"✅ تم الاتصال بملف Google Spreadsheet بنجاح: {SPREADSHEET_NAME}")
    except Exception as e:
        print(f"❌ خطأ أثناء فحص Google Sheets: {e}")


# ==========================================
# 3. إدارة المعاملات المالية (Transactions)
# ==========================================
@with_retry()
def _add_transactions_sync(user_key: str, tx_list: list):
    try:
        sheet = get_user_sheet(user_key)
        if not sheet:
            return []
        rows = []
        ids = []
        for item in tx_list:
            tx_id = item.get("tx_id") or f"TX-{uuid.uuid4().hex[:6].upper()}"
            ids.append(tx_id)
            img_url = item.get("image_url", "").strip()
            img_formula = f'=IMAGE("{img_url}")' if img_url else ""
            rows.append([
                item.get("date", get_now().strftime("%Y-%m-%d")),
                item.get("description", "بدون وصف"),
                float(item.get("amount", 0.0)),
                item.get("type", "مصروف"),
                item.get("category", "أخرى"),
                normalize_wallet(item.get("wallet", "نقدي")),
                "نشط",
                tx_id,
                img_formula,
            ])
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        return ids
    except Exception as e:
        print(f"❌ خطأ الإضافة للمستخدم {user_key}: {e}")
        return []


async def add_transactions(user_key: str, tx_list: list):
    return await asyncio.to_thread(_add_transactions_sync, user_key, tx_list)


def _add_to_sheet_sync(user_key: str, description, amount, trans_type, category, date_str, payment_method, custom_id=None, image_url=None):
    try:
        sheet = get_user_sheet(user_key)
        if not sheet:
            return None
        tx_id = custom_id if custom_id else f"TX-{uuid.uuid4().hex[:6].upper()}"
        img_formula = f'=IMAGE("{image_url.strip()}")' if (image_url and str(image_url).strip()) else ""
        row_data = [date_str, description, float(amount), trans_type, category, payment_method, "نشط", tx_id, img_formula]
        sheet.append_row(row_data, value_input_option="USER_ENTERED")
        return tx_id
    except Exception as e:
        print(f"❌ خطأ الإضافة للمستخدم {user_key}: {e}")
        return None


async def add_to_sheet(user_key: str, description, amount, trans_type, category, date_str, payment_method, custom_id=None, image_url=None):
    return await asyncio.to_thread(
        _add_to_sheet_sync, user_key, description, amount, trans_type, category, date_str, payment_method, custom_id, image_url
    )



def _update_tx_wallet_sync(user_key: str, tx_id: str, new_wallet: str):
    try:
        sheet = get_user_sheet(user_key)
        if not sheet:
            return False
        rows = sheet.get_all_values()
        for idx, r in enumerate(rows[1:], start=2):
            if len(r) > 7 and r[7].strip() == tx_id:
                sheet.update_cell(idx, 6, new_wallet)
                return True
        return False
    except Exception as e:
        print(f"❌ خطأ تحديث المحفظة: {e}")
        return False


async def update_tx_wallet(user_key: str, tx_id: str, new_wallet: str):
    return await asyncio.to_thread(_update_tx_wallet_sync, user_key, tx_id, new_wallet)


def _update_tx_amount_sync(user_key: str, tx_id: str, new_amt: float):
    try:
        sheet = get_user_sheet(user_key)
        if not sheet:
            return False
        rows = sheet.get_all_values()
        for idx, r in enumerate(rows[1:], start=2):
            if len(r) > 7 and r[7].strip() == tx_id:
                sheet.update_cell(idx, 3, float(new_amt))
                return True
        return False
    except Exception as e:
        print(f"❌ خطأ تعديل المبلغ: {e}")
        return False


async def update_tx_amount(user_key: str, tx_id: str, new_amt: float):
    return await asyncio.to_thread(_update_tx_amount_sync, user_key, tx_id, new_amt)


def _delete_from_sheet_sync(user_key: str, tx_ids: list, all_rows=None):
    try:
        sheet = get_user_sheet(user_key)
        if not sheet:
            return False
        if all_rows is None:
            all_rows = sheet.get_all_values()
        if not all_rows or len(all_rows) < 2:
            return False

        for target_id in tx_ids:
            for r_idx, row in enumerate(all_rows[1:], start=2):
                if any(str(target_id).strip() == str(cell).strip() for cell in row):
                    status_col = 7
                    for c_idx, cell in enumerate(row):
                        if str(cell).strip() in ["نشط", "ملغي"]:
                            status_col = c_idx + 1
                            break
                    sheet.update_cell(r_idx, status_col, "ملغي")
                    break
        return True
    except Exception as e:
        print(f"❌ خطأ الحذف للمستخدم {user_key}: {e}")
        return False


async def delete_from_sheet(user_key: str, tx_ids: list, all_rows=None):
    return await asyncio.to_thread(_delete_from_sheet_sync, user_key, tx_ids, all_rows)


@with_retry()
def _fetch_all_sync(user_key: str):
    try:
        sheet = get_user_sheet(user_key)
        if not sheet:
            return []
        all_rows = sheet.get_all_values()
        if not all_rows or len(all_rows) < 2:
            return []

        transactions = []
        for idx, row in enumerate(all_rows[1:], start=2):
            if not any(row):
                continue

            is_shifted = str(row[0]).strip().startswith("TX-")
            if is_shifted:
                tx_id_val = str(row[0]).strip()
                date_val = str(row[1]).strip() if len(row) > 1 else get_now().strftime("%Y-%m-%d")
                desc_val = str(row[2]).strip() if len(row) > 2 else "بدون وصف"
                raw_amt = str(row[3]) if len(row) > 3 else "0"
                type_val = str(row[4]).strip() if len(row) > 4 else "مصروف"
                cat_val = str(row[5]).strip() if len(row) > 5 else "أخرى"
                method_val = normalize_wallet(str(row[6])) if len(row) > 6 else "نقدي"
                status_val = str(row[7]).strip() if len(row) > 7 else "نشط"
            else:
                date_val = str(row[0]).strip() if len(row) > 0 else get_now().strftime("%Y-%m-%d")
                desc_val = str(row[1]).strip() if len(row) > 1 else "بدون وصف"
                raw_amt = str(row[2]) if len(row) > 2 else "0"
                type_val = str(row[3]).strip() if len(row) > 3 else "مصروف"
                cat_val = str(row[4]).strip() if len(row) > 4 else "أخرى"
                method_val = normalize_wallet(str(row[5])) if len(row) > 5 else "نقدي"
                status_val = str(row[6]).strip() if len(row) > 6 else "نشط"
                tx_id_val = str(row[7]).strip() if len(row) > 7 and row[7] else f"TX-{idx}"

            if "ملغي" in status_val or "archived" in status_val.lower():
                continue

            for ar_digit, en_digit in zip("٠١٢٣٤٥٦٧٨٩", "0123456789"):
                raw_amt = raw_amt.replace(ar_digit, en_digit)
            clean_amt = re.sub(r"[^\d.-]", "", raw_amt)
            try:
                amt = float(clean_amt)
            except (ValueError, TypeError):
                amt = 0.0

            img_val = str(row[8]).strip() if len(row) > 8 else ""

            transactions.append({
                "tx_id": tx_id_val,
                "date": date_val,
                "description": desc_val,
                "amount": amt,
                "type": type_val,
                "category": cat_val,
                "payment_method": method_val,
                "image_url": img_val,
            })


        return transactions
    except Exception as e:
        print(f"❌ خطأ قراءة البيانات للمستخدم {user_key}: {e}")
        return []


async def fetch_all_transactions(user_key: str):
    return await asyncio.to_thread(_fetch_all_sync, user_key)


# ==========================================
# 4. إدارة الديون والسلفيات (Debts)
# ==========================================
def normalize_ar_search(text: str) -> str:
    if not text:
        return ""
    s = str(text).lower().strip()
    s = re.sub(r"[أإآ]", "ا", s)
    s = re.sub(r"ة", "ه", s)
    s = re.sub(r"ى", "ي", s)
    return s


@with_retry()
def _add_debt_sync(user_key: str, person: str, amount: float, debt_type: str):
    try:
        sheet = get_debts_sheet(user_key)
        if not sheet:
            return None

        rows = sheet.get_all_values()
        date_str = get_now().strftime("%Y-%m-%d")
        norm_p = normalize_ar_search(person)
        opposite_type = "ليا" if debt_type == "عليا" else "عليا"
        remaining_to_add = float(amount)

        # مقاصة الديون المتقابلة تلقائياً
        if len(rows) >= 2:
            for idx, r in enumerate(rows[1:], start=2):
                if len(r) >= 8 and norm_p in normalize_ar_search(r[3]) and r[2].strip() == opposite_type and r[7].strip() == "نشط":
                    curr_rem = float(r[6] or 0)
                    if curr_rem <= 0:
                        continue

                    if remaining_to_add >= curr_rem:
                        remaining_to_add -= curr_rem
                        paid_val = float(r[5] or 0) + curr_rem
                        sheet.update_cell(idx, 6, paid_val)
                        sheet.update_cell(idx, 7, 0.0)
                        sheet.update_cell(idx, 8, "مسدد بالمقاصة")
                    else:
                        paid_val = float(r[5] or 0) + remaining_to_add
                        new_rem = curr_rem - remaining_to_add
                        sheet.update_cell(idx, 6, paid_val)
                        sheet.update_cell(idx, 7, new_rem)
                        remaining_to_add = 0.0
                        break

        if remaining_to_add > 0:
            d_id = f"DB-{uuid.uuid4().hex[:5].upper()}"
            sheet.append_row([d_id, date_str, debt_type, person, remaining_to_add, 0.0, remaining_to_add, "نشط"])
            return d_id
        return "SETTLED"
    except Exception as e:
        print(f"❌ خطأ تسجيل الدين: {e}")
        return None


async def add_debt(user_key: str, person: str, amount: float, debt_type: str):
    return await asyncio.to_thread(_add_debt_sync, user_key, person, amount, debt_type)


def _pay_debt_sync(user_key: str, person: str, amount: float):
    try:
        sheet = get_debts_sheet(user_key)
        if not sheet:
            return None
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return None
        norm_p = normalize_ar_search(person)
        for idx, r in enumerate(rows[1:], start=2):
            if len(r) >= 8 and norm_p in normalize_ar_search(r[3]) and r[7].strip() == "نشط":
                orig = float(r[4] or 0)
                paid = float(r[5] or 0) + float(amount)
                rem = max(0.0, orig - paid)
                status = "مسدد بالكامل" if rem <= 0 else "نشط"
                sheet.update_cell(idx, 6, paid)
                sheet.update_cell(idx, 7, rem)
                sheet.update_cell(idx, 8, status)
                return {"person": r[3], "paid": amount, "remaining": rem, "status": status, "type": r[2]}
        return None
    except Exception as e:
        print(f"❌ خطأ سداد الدين: {e}")
        return None


async def pay_debt(user_key: str, person: str, amount: float):
    return await asyncio.to_thread(_pay_debt_sync, user_key, person, amount)


def _delete_debt_sync(user_key: str, debt_id: str):
    try:
        sheet = get_debts_sheet(user_key)
        if not sheet or not debt_id:
            return False
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return False
        target_clean = str(debt_id).strip().lower()
        for idx, r in enumerate(rows[1:], start=2):
            if len(r) >= 8 and (r[0].strip().lower() == target_clean or target_clean in normalize_ar_search(r[3])):
                sheet.update_cell(idx, 8, "ملغي")
                sheet.update_cell(idx, 7, 0.0)
                return True
        return False
    except Exception as e:
        print(f"❌ خطأ حذف الدين: {e}")
        return False


async def delete_debt(user_key: str, debt_id: str):
    return await asyncio.to_thread(_delete_debt_sync, user_key, debt_id)


@with_retry()
def _fetch_debts_sync(user_key: str):
    try:
        sheet = get_debts_sheet(user_key)
        if not sheet:
            return []
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return []
        debts = []
        for r in rows[1:]:
            if len(r) >= 8 and any(r):
                debts.append({
                    "id": r[0], "date": r[1], "type": r[2], "person": r[3],
                    "original": float(r[4] or 0), "paid": float(r[5] or 0),
                    "remaining": float(r[6] or 0), "status": r[7],
                })
        return debts
    except Exception as e:
        print(f"❌ خطأ قراءة الديون: {e}")
        return []


async def fetch_debts(user_key: str):
    return await asyncio.to_thread(_fetch_debts_sync, user_key)


# ==========================================
# 5. إدارة الاشتراكات الشهرية (Subscriptions)
# ==========================================
def _add_sub_sync(user_key: str, name: str, amount: float, due_day: int, wallet="نقدي"):
    try:
        sheet = get_subs_sheet(user_key)
        if not sheet:
            return None
        s_id = f"SUB-{uuid.uuid4().hex[:4].upper()}"
        sheet.append_row([s_id, name, float(amount), int(due_day), wallet, "مفعل"])
        return s_id
    except Exception as e:
        print(f"❌ خطأ تسجيل الاشتراك: {e}")
        return None


async def add_subscription(user_key: str, name: str, amount: float, due_day: int, wallet="نقدي"):
    return await asyncio.to_thread(_add_sub_sync, user_key, name, amount, due_day, wallet)


def _fetch_subs_sync(user_key: str):
    try:
        sheet = get_subs_sheet(user_key)
        if not sheet:
            return []
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return []
        subs = []
        for r in rows[1:]:
            if len(r) >= 6 and any(r) and r[5].strip() == "مفعل":
                subs.append({
                    "id": r[0], "name": r[1], "amount": float(r[2] or 0),
                    "due_day": int(r[3] or 1), "wallet": r[4], "status": r[5],
                })
        return subs
    except Exception as e:
        print(f"❌ خطأ قراءة الاشتراكات: {e}")
        return []


async def fetch_subscriptions(user_key: str):
    return await asyncio.to_thread(_fetch_subs_sync, user_key)


def _delete_sub_sync(user_key: str, sub_id_or_name: str):
    try:
        sheet = get_subs_sheet(user_key)
        if not sheet or not sub_id_or_name:
            return False
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return False
        target_clean = str(sub_id_or_name).strip().lower()
        for idx, r in enumerate(rows[1:], start=2):
            if len(r) >= 6 and (r[0].strip().lower() == target_clean or target_clean in normalize_ar_search(r[1])):
                sheet.update_cell(idx, 6, "ملغي")
                return True
        return False
    except Exception as e:
        print(f"❌ خطأ حذف الاشتراك: {e}")
        return False


async def delete_subscription(user_key: str, sub_id_or_name: str):
    return await asyncio.to_thread(_delete_sub_sync, user_key, sub_id_or_name)

