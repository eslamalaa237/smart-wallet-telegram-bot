import ast
import asyncio
import json
import re
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, FALLBACK_MODELS, get_now
from sheets_db import get_monthly_budget, normalize_ar_search

# ==========================================
# 1. دوال استخراج النصوص ومعالجة JSON
# ==========================================
def extract_text(res) -> str:
    """استخراج النص الصافي من استجابة نماذج Gemini مع استبعاد أفكار التفكير (Thinking)."""
    if not res:
        return ""
    if getattr(res, "text", None):
        return res.text.strip()
    try:
        parts = res.candidates[0].content.parts
        non_thought_texts = [
            p.text for p in parts
            if hasattr(p, "text") and p.text and not getattr(p, "thought", False)
        ]
        if non_thought_texts:
            return "".join(non_thought_texts).strip()
        return "".join([p.text for p in parts if hasattr(p, "text") and p.text]).strip()
    except Exception:
        return ""


def parse_json_safely(raw_text: str):
    """تحليل نص JSON بشكل قوي حتى لو كانت الاستجابة تحتوي على markdown أو شوائب."""
    if not raw_text:
        return None
    raw = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if match:
        raw = match.group(1).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]

    try:
        return json.loads(raw)
    except Exception:
        pass

    try:
        val = ast.literal_eval(raw)
        if isinstance(val, dict):
            return val
    except Exception:
        pass

    try:
        fixed = re.sub(r"(?<=[{\s,])'([a-zA-Z0-9_]+)'\s*:", r'"\1":', raw)
        fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
        return json.loads(fixed)
    except Exception:
        pass

    return None


# ==========================================
# 2. البحث والمسار السريع للحذف
# ==========================================
def find_matching_transaction(transactions, amount=0.0, keyword="", is_last=False):
    if not transactions:
        return None

    reversed_txs = list(reversed(transactions))

    if is_last:
        return reversed_txs[0]

    norm_keyword = normalize_ar_search(keyword)

    if amount > 0 and norm_keyword:
        for t in reversed_txs:
            if abs(t["amount"] - amount) < 0.01:
                t_text = normalize_ar_search(f"{t['description']} {t['category']}")
                if norm_keyword in t_text or any(part in t_text for part in norm_keyword.split() if len(part) > 2):
                    return t

    if norm_keyword:
        for t in reversed_txs:
            t_text = normalize_ar_search(f"{t['description']} {t['category']}")
            if norm_keyword in t_text or any(part in t_text for part in norm_keyword.split() if len(part) > 2):
                return t

    if amount > 0:
        for t in reversed_txs:
            if abs(t["amount"] - amount) < 0.01:
                return t

    return None


def fast_parse_deletion(text: str):
    text_clean = text.strip().lower()
    delete_triggers = [
        "امسح", "احذف", "مسح", "الغي", "إلغي", "الغي", "الغى", "إلغى", "لغي",
        "شيل", "تراجع", "الغاء", "إلغاء", "delete", "remove", "undo", "لغيت", "مش عايز", "صفر"
    ]

    if not any(t in text_clean for t in delete_triggers):
        return None

    # 1. إلغاء وحذف الميزانية الشهرية أو ميزانية تصنيف
    if any(w in text_clean for w in ["الميزانية", "ميزانية", "budget"]):
        cat_candidate = text_clean
        for w in delete_triggers + ["الميزانية", "ميزانية", "budget", "بتاعة", "بتاع", "لتصنيف", "تصنيف", "الشهري", "الشهرية", "شهر"]:
            cat_candidate = re.sub(rf"\b{w}\b", "", cat_candidate)
        cat_candidate = re.sub(r"\s+", " ", cat_candidate).strip()
        if cat_candidate and len(cat_candidate) > 1:
            return {
                "intent": "delete_category_budget",
                "category": cat_candidate
            }
        return {
            "intent": "delete_budget"
        }

    # 2. إلغاء وحذف هدف الادخار والحصالة
    if any(w in text_clean for w in ["هدف", "الهدف", "حصالة", "الحصالة", "تحويش", "التحويش", "ادخار", "الادخار", "احوش", "أحوش", "goal"]):
        return {
            "intent": "delete_goal"
        }

    # 3. إلغاء وحذف اشتراك دوري معين
    if any(w in text_clean for w in ["اشتراك", "اشتراكات", "الاشتراك", "الاشتراكات", "subscription", "sub"]):
        sub_name = text_clean
        words_to_remove = delete_triggers + ["اشتراك", "اشتراكات", "الاشتراك", "الاشتراكات", "بتاع", "بتاعة", "شهري", "شهر", "خلاص", "عايز", "مش", "من"]
        for w in words_to_remove:
            sub_name = re.sub(rf"\b{w}\b", "", sub_name)
        sub_name = re.sub(r"\s+", " ", sub_name).strip()
        if sub_name:
            return {
                "intent": "delete_subscription",
                "name": sub_name
            }

    # 4. إلغاء وحذف قسط معين
    if any(w in text_clean for w in ["قسط", "اقساط", "أقساط", "القسط", "الأقساط", "installment"]):
        inst_name = text_clean
        words_to_remove = delete_triggers + ["قسط", "اقساط", "أقساط", "القسط", "الأقساط", "بتاع", "بتاعة", "شهري", "شهر", "خلاص", "عايز", "مش", "من"]
        for w in words_to_remove:
            inst_name = re.sub(rf"\b{w}\b", "", inst_name)
        inst_name = re.sub(r"\s+", " ", inst_name).strip()
        return {
            "intent": "delete_installment",
            "name": inst_name
        }

    is_last = any(w in text_clean for w in [
        "اخر عملية", "اخر حاجه", "اخر معاملة", "آخر عملية", "آخر حاجة",
        "اخر اجراء", "آخر إجراء", "اخر خطوة", "آخر خطوة", "اخر حاجة اتعملت",
        "آخر حاجة اتعملت", "اخر طلب", "آخر طلب", "آخر معامله", "اخر معامله",
        "اللي فات", "اللي لسه عامله", "اللي لسه مسجله", "اخر واحد", "آخر واحد"
    ])

    if is_last:
        return {
            "intent": "delete_last_action",
            "is_last": True,
        }

    nums = re.findall(r"\d+(?:\.\d+)?", text_clean)
    amount = float(nums[0]) if nums else 0.0

    cleaned_words = text_clean
    for t in delete_triggers + ["فلوس", "مصروف", "جنيه", "جنية", "بتاعة", "بتاع", "اللي", "ال"]:
        cleaned_words = re.sub(rf"\b{t}\b", "", cleaned_words)
    cleaned_words = re.sub(r"\d+", "", cleaned_words).strip()

    return {
        "intent": "delete_transaction",
        "amount": amount,
        "description": cleaned_words,
        "is_last": False,
    }



# ==========================================
# 3. المستشار المالي بالذكاء الاصطناعي
# ==========================================
def _get_financial_advice_sync(user_key: str, transactions):
    if not transactions:
        return "⚠️ لا توجد أي بيانات مالية مسجلة بعد في Google Sheets لتقديم استشارة!"

    now = get_now()
    start_date = now.strftime("%Y-%m-01")
    today_str = now.strftime("%Y-%m-%d")

    this_month_txs = [
        t for t in transactions
        if start_date <= t["date"] <= today_str and t.get("category") != "تحويلات"
    ]

    if not this_month_txs:
        return f"📅 لم تسجل أي معاملات خلال شهر {now.month} الحالي حتى الآن لنقوم بتحليلها!"

    inc_txs = [t for t in this_month_txs if t["type"] == "دخل"]
    exp_txs = [t for t in this_month_txs if t["type"] == "مصروف"]

    total_income = sum(t["amount"] for t in inc_txs)
    total_expense = sum(t["amount"] for t in exp_txs)
    net_savings = total_income - total_expense
    budget = get_monthly_budget(user_key)

    cat_breakdown = {}
    for t in exp_txs:
        cat = t["category"]
        cat_breakdown[cat] = cat_breakdown.get(cat, 0.0) + t["amount"]

    sorted_cats = sorted(cat_breakdown.items(), key=lambda x: x[1], reverse=True)
    cats_str = "\n".join([
        f"- {cat}: {amt:,.2f} ج.م ({(amt/total_expense*100) if total_expense > 0 else 0:.1f}%)"
        for cat, amt in sorted_cats
    ])

    burn_rate_daily = total_expense / max(now.day, 1)

    prompt = f"""
    أنت مستشار مالي شخصي ذكي وواقعي جداً، تتحدث بلهجة مصرية مهذبة وعملية واحترافية.
    حلل البيانات المالية للمستخدم لشهر {now.month} وقدم له تقييماً مالياً دقيقاً ونصائح محددة وقابلة للتطبيق فوراً.

    البيانات المالية الحالية للشهر:
    - الأيام المنقضية من الشهر: {now.day} يوم
    - إجمالي الدخل المحصل: {total_income:,.2f} ج.م
    - إجمالي المصاريف حتى الآن: {total_expense:,.2f} ج.م
    - معدل الصرف اليومي: {burn_rate_daily:,.2f} ج.م/يوم
    - الفائض / العجز الحالي: {net_savings:,.2f} ج.م
    - الميزانية الشهرية المحددة: {budget:,.2f} ج.م {'(لم تحدد ميزانية)' if budget <= 0 else ''}

    تفاصيل الصرف حسب البنود:
    {cats_str}

    المطلوب صياغة التقرير بتنسيق Markdown بالشكل التالي:
    💡 **الاستشارة والتقييم المالي لشهر {now.month}:**

    🩺 **التشخيص المالي:**
    (تقييم سريع لصحة الصرف: هل معدل الصرف متوازن مع أيام الشهر؟ هل هناك خطر عجز؟)

    ⚠️ **أكبر نقطة نزيف مالي:**
    (تسليط الضوء على البند الأكثر استنزافاً وتحليله بدقة)

    🎯 **توصيات عملية لتوفير الفلوس:**
    (2 إلى 3 نصائح عملية ومباشرة قابلة للتطبيق فيما تبقى من الشهر بناءً على أرقامه المذكورة)

    🚀 **تحدي الأيام القادمة:**
    (تحديد رقم يومي مقترح للصرف أو هدف ادخار ملموس)
    """

    client = genai.Client(api_key=GEMINI_API_KEY)

    for m in FALLBACK_MODELS:
        try:
            try:
                cfg = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    max_output_tokens=1000,
                )
            except Exception:
                cfg = types.GenerateContentConfig(max_output_tokens=1000)

            res = client.models.generate_content(model=m, contents=prompt, config=cfg)
            txt = extract_text(res)
            if txt:
                return txt
        except Exception:
            continue

    return "⚠️ تعذر الاتصال بمحرك الذكاء الاصطناعي حالياً، يرجى المحاولة بعد قليل."


async def get_financial_advice(user_key: str, transactions):
    return await asyncio.to_thread(_get_financial_advice_sync, user_key, transactions)


# ==========================================
# 4. تحليل صور الفواتير (Receipt OCR)
# ==========================================
def _analyze_receipt_sync(photo_bytes):
    prompt = """
    أنت ماسح ضوئي ذكي للفواتير والإيصالات. استخرج البيانات بدقة من صورة الفاتورة بتنسيق JSON فقط:
    1. intent: دائماً "multi_actions"
    2. actions: مصفوفة تحتوي على العمليات:
       - action: "transaction"
       - type: "مصروف" (أو "دخل" لو إيصال تحويل وارد)
       - amount: المبلغ الإجمالي النهائي (رقم موجب)
       - description: اسم المكان وأهم صنف باختصار (مثال: "كارفور - مشتريات ماركت")
       - category: تصنيف المعاملة (طعام ومشروبات، تسوق، فواتير، صحة، مواصلات، ترفيه، أخرى)
       - wallet: دائماً "نقدي" (إلا إذا كُتب صراحةً في الفاتورة دفع فيزا/بطاقة بنكية فتكون "فيزا")

    JSON فقط:
    {"intent": "multi_actions", "actions": [{"action": "transaction", "amount": 0.0, "type": "مصروف", "description": "...", "category": "...", "wallet": "نقدي"}]}
    """
    image_part = types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg")
    client = genai.Client(api_key=GEMINI_API_KEY)

    for m in FALLBACK_MODELS:
        try:
            try:
                cfg = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=500,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                )
            except Exception:
                cfg = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=500,
                )

            res = client.models.generate_content(
                model=m,
                contents=[image_part, prompt],
                config=cfg,
            )
            raw = extract_text(res)
            parsed = parse_json_safely(raw)
            if parsed:
                return parsed
        except Exception:
            continue

    return {"intent": "error"}


async def analyze_receipt_image(photo_bytes):
    return await asyncio.to_thread(_analyze_receipt_sync, photo_bytes)


# ==========================================
# 5. محرك الفهم الذكي المطور (Multi-Action Engine)
# ==========================================
def _analyze_sync(content_input, is_audio, mime_type):
    now = get_now()
    today_str = now.strftime("%Y-%m-%d")
    weekday_ar = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"][now.weekday()]

    prompt = f"""
    أنت مساعد مالي ذكي بالعامية المصرية. حلل الرسالة واستخرج كل الأوامر والعمليات الواردة فيها بدقة بتنسيق JSON فقط.
    تاريخ اليوم: {today_str} (يوم {weekday_ar}).

    📌 ميزة تسجيل العمليات السابقة (Backdating):
    إذا ذكر المستخدم تاريخاً أو يوماً في الماضي (مثل: "امبارح", "أمس", "أول امبارح", "الخميس اللي فات", "يوم 15 الشهر ده", "بتاريخ 2026-09-20"):
    احسب التاريخ الدقيق بالصيغة YYYY-MM-DD وضعه في الحقل "date" لكل عملية.
    إذا لم يذكر تاريخاً أو كانت المعاملة في الحاضر، اجعل "date" دائماً هو "{today_str}".

    ملاحظة هامة جداً:
    المستخدم قد يرسل في نفس الرسالة عدة أسطر أو عدة عمليات مختلفة (مثلاً: استلفت من كذا، وحولت من كذا لكذا، وصرفت كذا).
    يجب عليك تفكيك الرسالة إلى قائمة إجراءات مجمعة تحت "intent": "multi_actions" ومصفوفة "actions".

    أنواع العمليات في "actions":
    1. الديون والسلفيات:
       - استلفت / دين عليا (مثل: "استلفت من حسام 1600" أو "استفلت من زياد 1200"):
         {{"action": "debt_add", "person": "اسم الشخص", "amount": 1600, "debt_type": "عليا", "date": "{today_str}"}}
       - سلفت / دين ليا (مثل: "سلفت محمد 500"):
         {{"action": "debt_add", "person": "اسم الشخص", "amount": 500, "debt_type": "ليا", "date": "{today_str}"}}
       - سداد دين:
         {{"action": "debt_pay", "person": "اسم الشخص", "amount": 200, "wallet": "نقدي"|"فيزا"|"حصالة", "date": "{today_str}"}}

    2. التحويلات بين المحافظ وحصالة الادخار:
       - مثل: "حول من الحصاله للنقدى 2500" أو "حول من الفيزا للحصالة 1000" أو "حطيت 500 في الحصالة" أو "شلت 1000 في الحصالة" أو "وفرت وحوشت 300":
         {{"action": "transfer", "amount": 500, "from_wallet": "نقدي", "to_wallet": "حصالة", "date": "{today_str}"}}
       - السحب من الحصالة: "سحبت 300 من الحصالة" أو "خدت من الحصالة 500":
         {{"action": "transfer", "amount": 300, "from_wallet": "حصالة", "to_wallet": "نقدي", "date": "{today_str}"}}

    3. المعاملات المالية العادية (مصروف أو دخل):
       - مثل: "دفعت 50 مواصلات و 150 غدا" أو "صرفت أول امبارح 400 بنزين" أو "قبضت 5000 راتب فيزا":
         {{"action": "transaction", "amount": 50, "description": "مواصلات", "type": "مصروف", "category": "مواصلات", "wallet": "نقدي", "date": "{today_str}"}}
         (القاعدة: إذا لم يحدد المحفظة فهي "نقدي" تلقائياً، وإذا ذكر تاريخاً سابقاً ضعه في "date").

    4. الاشتراكات الشهرية والدورية (بدون الحاجة لكتابة أمر):
       - عندما يذكر المستخدم اشتراكاً ومبلغه ويوم استحقاقه، مثل:
         • "انا هدفع اشتراك النت ب 500ج يوم 10"
         • "سجل اشتراك الجيم 400 جنيه يوم 1"
         • "عندي اشتراك نتفلكس 250 فيزا يوم 15"
         {{"action": "sub_add", "name": "اشتراك النت", "amount": 500, "due_day": 10, "wallet": "نقدي"}}

    5. الأقساط والالتزامات الشهرية:
       - إضافة قسط جديد: (مثل: "عليا قسط تكييف 750 جنيه شهريا لمدة 10 شهور يوم 5" أو "سجل قسط موبايل 1000 شهريا 6 شهور"):
         {{"action": "installment_add", "name": "تكييف", "monthly_amount": 750, "total_months": 10, "due_day": 5, "wallet": "نقدي"}}
       - سداد قسط: (مثل: "سددت قسط التكييف" أو "دفعت قسط الموبايل فيزا"):
         {{"action": "installment_pay", "name": "تكييف", "wallet": "نقدي"}}

    6. حصالة الادخار وتحديد هدف الادخار (بدون الحاجة لكتابة أمر):
       - عندما يطلب تحديد هدف تحويش، مثل:
         • "عايز أحوش 20000 للابتوب" أو "هدفي أحوش 50000 لعربية" أو "حط هدف تحويش 15000":
         {{"action": "set_goal", "goal_title": "لابتوب", "amount": 20000}}

    7. ميزانية مخصصة لتصنيف معين:
       - مثل: "حط ميزانية للاكل 3000" أو "ميزانية مواصلات 1000":
         {{"action": "set_category_budget", "category": "طعام ومشروبات", "amount": 3000}}

    8. تقسيم الفواتير والحساب المشترك (سواء بالتساوي أو بتحديد كم دفع كل شخص وكم عليه):
       - المستخدم قد يقسم بالتساوي أو يحدد مبالغ مختلفة (شخص يدفع أكثر أو أقل، ونصيب كل شخص مختلف).
       - الحقول:
         * action: "split_bill"
         * description: وصف الفاتورة (مثل: "غدا", "كافيه", "عشاء")
         * total_amount: إجمالي قيمة الفاتورة كاملة (رقم موجب)
         * wallet: محفظة المستخدم إذا دفع منها (نقدي أو فيزا، الافتراضي "نقدي")
         * user_paid: المبلغ الذي دفعه المستخدم (أنا) من جيبه/محفظته في الفاتورة (إذا دفع المستخدم الفاتورة كلها فهو يساوي total_amount، وإذا لم يدفع شيئاً فهو 0)
         * user_share: نصيب المستخدم الشخصي الفعلي الذي يعتبر مصروفه الخاص به
         * date: تاريخ الفاتورة YYYY-MM-DD
         * participants: قائمة بكل الأفراد المشاركين الآخرين (ما دفعه كل شخص وما هو مستحق عليه):
           [
             {{"name": "أحمد", "paid": ما دفعه هذا الشخص (0 لو مدفعش), "share": نصيب هذا الشخص المستحق عليه}}
           ]
       - أمثلة:
         • "فاتورة كافيه 600 أنا دفعتها نقدي: أنا عليا 200 وأحمد 300 ومصطفى 100":
           {{"action": "split_bill", "description": "كافيه", "total_amount": 600, "wallet": "نقدي", "user_paid": 600, "user_share": 200, "date": "{today_str}", "participants": [{{"name": "أحمد", "paid": 0, "share": 300}}, {{"name": "مصطفى", "paid": 0, "share": 100}}]}}

    9. الاستعلامات والأوامر المفردة والإلغاء:
       - يمكنك إرجاع الـ intent المباشر مثل: "query_debts", "query_balance", "query_report", "query_income", "query_chart", "query_excel", "delete_transaction", "delete_budget", "delete_goal", "delete_subscription", "delete_installment", "delete_category_budget", "query_advice", "set_budget", "set_goal", "query_goal", "query_yearly", "query_compare", "query_installments", "query_monthly_closing".
       - استعلام الأقساط: (مثل: "الأقساط اللي عليا", "موقف الأقساط", "جدول الأقساط"):
         {{"intent": "query_installments"}}
       - إلغاء قسط: (مثل: "الغي قسط التكييف", "احذف قسط الموبايل"):
         {{"intent": "delete_installment", "name": "اسم القسط"}}
       - إلغاء ميزانية تصنيف: (مثل: "الغي ميزانية الاكل", "احذف ميزانية التسوق"):
         {{"intent": "delete_category_budget", "category": "طعام ومشروبات"}}
       - التقرير الختامي الشهري: (مثل: "تقرير الشهر اللي فات", "ملخص ختام الشهر", "تقفيل الشهر"):
         {{"intent": "query_monthly_closing"}}
       - إلغاء الميزانية الشهرية العامة: (مثل: "الغي الميزانية", "احذف الميزانية", "مش عايز ميزانية"):
         {{"intent": "delete_budget"}}
       - إلغاء هدف الادخار: (مثل: "الغي هدف الادخار", "احذف هدف الحصالة", "امسح الهدف", "مش عايز أحوش لموبايل خلاص"):
         {{"intent": "delete_goal"}}
       - إلغاء اشتراك دوري: (مثل: "الغي اشتراك الجيم", "احذف اشتراك النت", "امسح اشتراك نتفلكس"):
         {{"intent": "delete_subscription", "name": "اسم الاشتراك"}}
       - استعلام الحصالة والادخار: (مثل: "معايا كام في الحصالة؟" أو "موقف هدف الادخار"):
         {{"intent": "query_goal"}}
       - استعلام الديون لشخص محدد (مثل: "عليا كام لأحمد؟"):
         {{"intent": "query_debts", "person": "أحمد"}}
       - استعلام تقرير سنوي (مثل: "تقرير سنة 2024" أو "تقرير سنوي" أو "كشف السنة"):
         {{"intent": "query_yearly", "year": 2024}}
       - مقارنة بين الشهور (مثل: "قارن بين الشهر ده والشهر اللي فات"):
         {{"intent": "query_compare"}}


    إذا كانت الرسالة تحتوي على عملية تنفيذية واحدة أو أكثر (سلف، تحويل، صرف، دخل، اشتراك، حصالة، تقسيم فاتورة)، اجعل الـ intent دائماً "multi_actions":
    JSON مثال لرسالة مركبة:
    {{
      "intent": "multi_actions",
      "actions": [
        {{"action": "debt_add", "person": "حسام", "amount": 1600, "debt_type": "عليا"}},
        {{"action": "sub_add", "name": "اشتراك النت", "amount": 500, "due_day": 10, "wallet": "نقدي"}},
        {{"action": "transfer", "amount": 2500, "from_wallet": "حصالة", "to_wallet": "نقدي"}}
      ]
    }}

    JSON فقط بدون أي markdown:
    """

    contents = []
    if is_audio:
        contents.append(types.Part.from_bytes(data=content_input, mime_type=mime_type))
    else:
        contents.append(content_input)
    contents.append(prompt)

    client = genai.Client(api_key=GEMINI_API_KEY)

    for model_name in FALLBACK_MODELS:
        try:
            try:
                cfg = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=1200,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                )
            except Exception:
                cfg = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=1200,
                )

            res = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=cfg,
            )

            raw = extract_text(res)
            parsed = parse_json_safely(raw)
            if parsed:
                print(f"⚡ استجابة ناجحة عبر ({model_name}): {parsed.get('intent')}")
                return parsed
        except Exception:
            continue

    return {"intent": "error"}


async def analyze_user_request(content_input, is_audio=False, mime_type="audio/ogg"):
    return await asyncio.to_thread(_analyze_sync, content_input, is_audio, mime_type)
