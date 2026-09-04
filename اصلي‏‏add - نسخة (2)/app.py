# -*- coding: utf-8 -*-
"""
سيرفر إدارة وسحب وإضافة أعضاء تيليجرام المتكامل
Flask + Telethon + Flask-SocketIO + Asyncio Bridge
"""

import os
import sys
import json
import time
import random
import asyncio
import threading

# ضمان توافق تشفير UTF-8 للطباعة على أنظمة ويندوز
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, Response
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, functions
from telethon.errors import (
    UserPrivacyRestrictedError,
    PeerFloodError,
    FloodWaitError,
    UserNotMutualContactError,
    ChatAdminRequiredError,
    InputUserDeactivatedError,
    UsernameNotOccupiedError,
    UserBannedInChannelError,
    UserKickedError,
    UserAlreadyParticipantError,
    SessionPasswordNeededError,
    PhoneNumberBannedError,
    AuthKeyUnregisteredError,
    ChannelPrivateError
)
from telethon.tl.types import (
    UserStatusOnline,
    UserStatusRecently,
    ChannelParticipantsSearch
)

# ================== تهيئة التطبيق والمسارات ==================
app = Flask(__name__)
app.config["SECRET_KEY"] = "telegram-adder-pro-suite-2026"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
DATA_DIR = os.path.join(BASE_DIR, "data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
MEMBERS_FILE = os.path.join(DATA_DIR, "members.txt")
PROGRESS_FILE = os.path.join(DATA_DIR, "progress.json")

# التأكد من وجود المجلدات الأساسية
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

if not os.path.exists(ACCOUNTS_FILE):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

if not os.path.exists(MEMBERS_FILE):
    with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
        f.write("")

if not os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "current_index": 0,
            "total": 0,
            "target_group": "",
            "stats": {"success": 0, "failed": 0, "skipped": 0, "already_member": 0, "disabled_accounts": 0}
        }, f, indent=2)


# ================== جسر الـ Asyncio لـ Telethon ==================
# حلقة أحداث مخصصة تعمل في خيط منفصل لإدارة كافة اتصالات Telethon غير المتزامنة
bg_loop = asyncio.new_event_loop()

def run_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

loop_thread = threading.Thread(target=run_background_loop, args=(bg_loop,), daemon=True)
loop_thread.start()

def run_async(coro, timeout=60):
    """تنفيذ Coroutine على حلقة الأحداث الخلفية واسترجاع النتيجة بشكل آمن ومتزامن"""
    future = asyncio.run_coroutine_threadsafe(coro, bg_loop)
    return future.result(timeout=timeout)


# ================== المتغيرات العامة وحالات العمليات ==================
# إدارة جلسات تسجيل الدخول المعلقة: {phone: {client, phone_code_hash, api_id, api_hash, session, created_at}}
pending_auth = {}

# حالة عملية الإضافة
addition_running = False
addition_paused = False
addition_thread = None
addition_stop_event = threading.Event()
addition_pause_event = threading.Event()
addition_pause_event.set()  # set يعني غير متوقف مؤقتاً

# حالة عملية السحب
scraper_running = False
scraper_thread = None
scraper_stop_event = threading.Event()


# ================== دوال مساعدة لحفظ وقراءة البيانات ==================
def load_accounts():
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)

def load_progress():
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"current_index": 0, "total": 0, "target_group": "", "stats": {}}

def save_progress(data):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ================== المسارات (Routes) ==================

@app.route("/")
def index():
    return render_template("index.html")


# ---------- إدارة الحسابات (Account APIs) ----------

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    """جلب قائمة الحسابات والتحقق السريع من وجود ملف الجلسة"""
    accounts = load_accounts()
    result = []
    for acc in accounts:
        session_file = acc["session"] + ".session"
        file_exists = os.path.exists(session_file)
        result.append({
            "id": acc.get("id", 0),
            "name": acc.get("name", "غير معروف"),
            "username": acc.get("username", ""),
            "phone": acc.get("phone", ""),
            "user_id": acc.get("user_id", ""),
            "has_session": file_exists,
            "active": acc.get("active", file_exists),
            "created_at": acc.get("created_at", "")
        })
    return jsonify({"success": True, "accounts": result})


@app.route("/api/send_code", methods=["POST"])
def send_code():
    """بدء عملية إضافة الحساب وإرسال رمز التحقق"""
    data = request.json or {}
    api_id = str(data.get("api_id", "")).strip()
    api_hash = str(data.get("api_hash", "")).strip()
    phone = str(data.get("phone", "")).strip()

    if not api_id or not api_hash or not phone:
        return jsonify({"success": False, "message": "جميع الحقول (API ID, API HASH, رقم الهاتف) مطلوبة!"}), 400

    try:
        api_id_int = int(api_id)
    except ValueError:
        return jsonify({"success": False, "message": "API ID يجب أن يكون رقماً صحيحاً!"}), 400

    clean_phone = phone.replace("+", "").replace(" ", "")
    session_path = os.path.join(SESSIONS_DIR, clean_phone)

    # تنظيف أي محاولة قديمة لنفس الرقم
    if clean_phone in pending_auth:
        try:
            old_client = pending_auth[clean_phone].get("client")
            if old_client:
                run_async(old_client.disconnect(), timeout=5)
        except Exception:
            pass
        pending_auth.pop(clean_phone, None)

    async def _async_send():
        client = TelegramClient(session_path, api_id_int, api_hash, loop=bg_loop)
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            # الحساب مسجل بالفعل، نقوم بتحديثه في accounts.json
            accounts = load_accounts()
            existing = next((a for a in accounts if a["phone"] == phone), None)
            if not existing:
                account_id = len(accounts) + 1
                accounts.append({
                    "id": account_id,
                    "phone": phone,
                    "api_id": api_id_int,
                    "api_hash": api_hash,
                    "session": session_path,
                    "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                    "username": me.username or "",
                    "user_id": me.id,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                save_accounts(accounts)
            return {"success": True, "already_authorized": True, "message": f"الحساب مسجل ومصرح به مسبقاً باسم: {me.first_name}"}

        sent = await client.send_code_request(phone)
        pending_auth[clean_phone] = {
            "client": client,
            "phone_code_hash": sent.phone_code_hash,
            "api_id": api_id_int,
            "api_hash": api_hash,
            "phone": phone,
            "session": session_path,
            "created_at": time.time()
        }
        return {"success": True, "already_authorized": False, "message": f"تم إرسال كود التحقق بنجاح إلى الرقم {phone}"}

    try:
        res = run_async(_async_send(), timeout=30)
        return jsonify(res)
    except PhoneNumberBannedError:
        return jsonify({"success": False, "message": "هذا الرقم محظور من استخدام تيليجرام!"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ أثناء إرسال الكود: {str(e)}"}), 500


@app.route("/api/verify_code", methods=["POST"])
def verify_code():
    """التحقق من الكود المدخل وكلمة مرور التحقق بخطوتين (2FA) وحفظ الحساب"""
    data = request.json or {}
    phone = str(data.get("phone", "")).strip()
    code = str(data.get("code", "")).strip()
    password = str(data.get("password", "")).strip()

    clean_phone = phone.replace("+", "").replace(" ", "")
    auth_data = pending_auth.get(clean_phone)

    if not auth_data:
        return jsonify({"success": False, "message": "انتهت مهلة التحقق أو لم يتم إرسال كود لهذا الرقم. يرجى إعادة الإرسال."}), 400

    client = auth_data["client"]

    async def _async_verify():
        try:
            await client.sign_in(
                phone=auth_data["phone"],
                code=code,
                phone_code_hash=auth_data["phone_code_hash"]
            )
        except SessionPasswordNeededError:
            if password:
                await client.sign_in(password=password)
            else:
                return {
                    "success": False,
                    "needs_password": True,
                    "message": "هذا الحساب محمي بكلمة مرور التحقق بخطوتين (2FA)، يرجى إدخالها."
                }

        me = await client.get_me()
        await client.disconnect()

        # إضافة الحساب
        accounts = load_accounts()
        # إزالة الحساب القديم إذا كان مكرراً
        accounts = [a for a in accounts if a.get("phone") != auth_data["phone"]]
        account_id = len(accounts) + 1

        new_acc = {
            "id": account_id,
            "phone": auth_data["phone"],
            "api_id": auth_data["api_id"],
            "api_hash": auth_data["api_hash"],
            "session": auth_data["session"],
            "name": f"{me.first_name or ''} {me.last_name or ''}".strip() or "بدون اسم",
            "username": me.username or "",
            "user_id": me.id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        accounts.append(new_acc)
        save_accounts(accounts)

        # تنظيف الجلسة المؤقتة
        pending_auth.pop(clean_phone, None)

        return {
            "success": True,
            "message": f"تم تسجيل الدخول وإضافة الحساب بنجاح: {new_acc['name']}",
            "account": new_acc
        }

    try:
        res = run_async(_async_verify(), timeout=35)
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ في التحقق: {str(e)}"}), 400


@app.route("/api/accounts/check", methods=["POST"])
def check_accounts_health():
    """فحص صحة جميع الحسابات والتأكد من أنها متصلة وصالحة"""
    accounts = load_accounts()
    if not accounts:
        return jsonify({"success": False, "message": "لا توجد حسابات مسجلة للفحص"}), 400

    async def _async_check():
        results = []
        for acc in accounts:
            client = TelegramClient(acc["session"], acc["api_id"], acc["api_hash"], loop=bg_loop)
            status_text = "سليم"
            is_active = False
            try:
                await client.connect()
                if await client.is_user_authorized():
                    me = await client.get_me()
                    is_active = True
                    acc["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip()
                    acc["username"] = me.username or ""
                    acc["active"] = True
                else:
                    status_text = "انتهت الجلسة (بحاجة لإعادة تسجيل)"
                    acc["active"] = False
            except PhoneNumberBannedError:
                status_text = "محظور من تيليجرام!"
                acc["active"] = False
            except AuthKeyUnregisteredError:
                status_text = "مفتاح الجلسة ملغى"
                acc["active"] = False
            except Exception as e:
                status_text = f"خطأ: {str(e)}"
                acc["active"] = False
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            results.append({
                "id": acc["id"],
                "phone": acc["phone"],
                "name": acc.get("name", ""),
                "active": is_active,
                "status_text": status_text
            })

        save_accounts(accounts)
        return results

    try:
        check_results = run_async(_async_check(), timeout=len(accounts) * 15 + 10)
        return jsonify({"success": True, "results": check_results})
    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ أثناء فحص الحسابات: {str(e)}"}), 500


@app.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account(acc_id):
    """حذف حساب وإزالة ملف الجلسة الخاص به"""
    accounts = load_accounts()
    target_acc = next((a for a in accounts if a.get("id") == acc_id), None)

    if not target_acc:
        return jsonify({"success": False, "message": "الحساب غير موجود"}), 404

    session_file = target_acc["session"] + ".session"
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
        except Exception:
            pass

    new_accounts = [a for a in accounts if a.get("id") != acc_id]
    save_accounts(new_accounts)
    return jsonify({"success": True, "message": "تم حذف الحساب وملف جلسته بنجاح"})


# ---------- إدارة قائمة الأعضاء (Members List APIs) ----------

@app.route("/api/members", methods=["GET"])
def get_members():
    """تحميل قائمة الأعضاء من ملف members.txt"""
    if os.path.exists(MEMBERS_FILE):
        with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
            lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
            return jsonify({"success": True, "members_text": content, "count": len(lines)})
    return jsonify({"success": True, "members_text": "", "count": 0})


@app.route("/api/members", methods=["POST"])
def save_members_list():
    """حفظ قائمة الأعضاء المدخلة"""
    data = request.json or {}
    members_text = data.get("members", "")
    with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
        f.write(members_text)
    lines = [l.strip() for l in members_text.split("\n") if l.strip() and not l.strip().startswith("#")]
    return jsonify({"success": True, "message": "تم حفظ قائمة الأعضاء بنجاح", "count": len(lines)})


@app.route("/api/members/export", methods=["GET"])
def export_members():
    """تنزيل قائمة الأعضاء كملف نصي"""
    if os.path.exists(MEMBERS_FILE):
        return send_file(MEMBERS_FILE, as_attachment=True, download_name="telegram_members.txt", mimetype="text/plain")
    return Response("الملف غير موجود", status=404)


# ---------- محرك سحب الأعضاء (Scraper APIs & Engine) ----------

@app.route("/api/scraper/start", methods=["POST"])
def start_scraping():
    global scraper_running, scraper_thread, scraper_stop_event

    if scraper_running:
        return jsonify({"success": False, "message": "عملية السحب قيد التشغيل بالفعل!"}), 400

    data = request.json or {}
    source_group = data.get("source_group", "").strip()
    account_id = data.get("account_id")
    filter_active = bool(data.get("filter_active", False))
    filter_bots = bool(data.get("filter_bots", True))
    filter_has_username = bool(data.get("filter_has_username", False))
    limit = int(data.get("limit", 0))

    if not source_group:
        return jsonify({"success": False, "message": "يرجى إدخال معرف أو رابط المجموعة المصدر!"}), 400

    accounts = load_accounts()
    if not accounts:
        return jsonify({"success": False, "message": "لا توجد حسابات مسجلة لاستخدامها في السحب!"}), 400

    # اختيار الحساب المطلوب أو أول حساب
    selected_acc = None
    if account_id:
        selected_acc = next((a for a in accounts if a.get("id") == int(account_id)), None)
    if not selected_acc:
        selected_acc = accounts[0]

    scraper_running = True
    scraper_stop_event.clear()

    scraper_thread = threading.Thread(
        target=run_scraper_worker,
        args=(selected_acc, source_group, filter_active, filter_bots, filter_has_username, limit),
        daemon=True
    )
    scraper_thread.start()

    return jsonify({
        "success": True,
        "message": f"تم بدء سحب الأعضاء باستخدام حساب: {selected_acc.get('name', selected_acc.get('phone'))}",
        "account": selected_acc.get("phone")
    })


@app.route("/api/scraper/stop", methods=["POST"])
def stop_scraping():
    global scraper_running
    scraper_stop_event.set()
    scraper_running = False
    socketio.emit("scraper_log", {"type": "warning", "message": "⏹️ تم إيقاف عملية سحب الأعضاء بواسطة المستخدم"})
    return jsonify({"success": True, "message": "تم إيقاف عملية السحب"})


def run_scraper_worker(account_info, source_group, filter_active, filter_bots, filter_has_username, limit):
    global scraper_running

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _async_scrape():
        client = TelegramClient(account_info["session"], account_info["api_id"], account_info["api_hash"], loop=loop)
        await client.connect()

        if not await client.is_user_authorized():
            socketio.emit("scraper_log", {"type": "error", "message": f"❌ جلسة الحساب {account_info['phone']} منتهية!"})
            await client.disconnect()
            return

        socketio.emit("scraper_log", {"type": "info", "message": f"🔍 جاري فحص المجموعة المصدر: {source_group}..."})

        try:
            entity = await client.get_entity(source_group)
            group_title = getattr(entity, "title", source_group)
            socketio.emit("scraper_log", {"type": "success", "message": f"✅ تم الوصول إلى: {group_title}"})
        except Exception as e:
            socketio.emit("scraper_log", {"type": "error", "message": f"❌ تعذر الوصول للمجموعة: {str(e)}"})
            await client.disconnect()
            return

        socketio.emit("scraper_log", {"type": "info", "message": "🚀 بدء استخراج الأعضاء وتطبيق الفلاتر..."})

        scraped_list = []
        count = 0

        try:
            async for user in client.iter_participants(entity, limit=limit if limit > 0 else None, aggressive=True):
                if scraper_stop_event.is_set():
                    break

                # تطبيق الفلاتر
                if filter_bots and getattr(user, "bot", False):
                    continue

                if filter_active:
                    status = getattr(user, "status", None)
                    if not isinstance(status, (UserStatusOnline, UserStatusRecently)):
                        continue

                username = getattr(user, "username", None)
                if filter_has_username and not username:
                    continue

                identifier = f"@{username}" if username else str(user.id)
                scraped_list.append(identifier)
                count += 1

                if count % 25 == 0 or count <= 10:
                    socketio.emit("scraper_progress", {
                        "count": count,
                        "latest": identifier,
                        "name": f"{user.first_name or ''} {user.last_name or ''}".strip()
                    })

            # إرسال النتيجة النهائية
            socketio.emit("scraper_log", {"type": "success", "message": f"🎉 تم استخراج {len(scraped_list)} عضو بنجاح!"})
            socketio.emit("scraper_complete", {
                "total": len(scraped_list),
                "members": scraped_list
            })

        except Exception as e:
            socketio.emit("scraper_log", {"type": "error", "message": f"❌ خطأ أثناء السحب: {str(e)}"})
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    try:
        loop.run_until_complete(_async_scrape())
    finally:
        loop.close()
        scraper_running = False


# ---------- محرك إضافة الأعضاء (Adder APIs & Engine) ----------

@app.route("/api/adder/start", methods=["POST"])
def start_addition():
    global addition_running, addition_paused, addition_thread, addition_stop_event, addition_pause_event

    if addition_running and not addition_paused:
        return jsonify({"success": False, "message": "عملية الإضافة جارية بالفعل!"}), 400

    data = request.json or {}
    group_input = data.get("group_id", "").strip()
    delay_min = int(data.get("delay_min", 10))
    delay_max = int(data.get("delay_max", 20))
    max_per_account = int(data.get("max_per_account", 25))
    resume = bool(data.get("resume", False))
    members_text = data.get("members", "")

    if not group_input:
        return jsonify({"success": False, "message": "يرجى إدخال معرف أو رابط المجموعة الهدف!"}), 400

    # استخراج قائمة الأعضاء
    members = [
        line.strip().lstrip("@").strip()
        for line in members_text.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]

    if not members:
        return jsonify({"success": False, "message": "قائمة الأعضاء فارغة!"}), 400

    accounts = load_accounts()
    if not accounts:
        return jsonify({"success": False, "message": "لا توجد حسابات مسجلة للإضافة!"}), 400

    # تحديد مؤشر البداية
    start_index = 0
    stats = {"success": 0, "failed": 0, "skipped": 0, "already_member": 0, "disabled_accounts": 0}

    if resume:
        saved_prog = load_progress()
        if saved_prog.get("current_index", 0) < len(members):
            start_index = saved_prog.get("current_index", 0)
            stats = saved_prog.get("stats", stats)

    addition_running = True
    addition_paused = False
    addition_stop_event.clear()
    addition_pause_event.set()

    addition_thread = threading.Thread(
        target=run_addition_worker,
        args=(accounts, group_input, members, delay_min, delay_max, max_per_account, start_index, stats),
        daemon=True
    )
    addition_thread.start()

    return jsonify({
        "success": True,
        "message": "تم بدء عملية الإضافة بنجاح",
        "total_members": len(members),
        "start_index": start_index,
        "total_accounts": len(accounts)
    })


@app.route("/api/adder/pause", methods=["POST"])
def pause_addition():
    global addition_paused
    if not addition_running:
        return jsonify({"success": False, "message": "لا توجد عملية نشطة لإيقافها مؤقتاً"}), 400
    addition_paused = True
    addition_pause_event.clear()
    socketio.emit("log", {"type": "warning", "message": "⏸️ تم إيقاف العملية مؤقتاً. يمكنك الاستئناف في أي وقت."})
    return jsonify({"success": True, "message": "تم الإيقاف المؤقت"})


@app.route("/api/adder/resume", methods=["POST"])
def resume_addition():
    global addition_paused
    if not addition_running:
        return jsonify({"success": False, "message": "لا توجد عملية متوقفة مؤقتاً"}), 400
    addition_paused = False
    addition_pause_event.set()
    socketio.emit("log", {"type": "info", "message": "▶️ تم استئناف عملية الإضافة بنجاح."})
    return jsonify({"success": True, "message": "تم الاستئناف"})


@app.route("/api/adder/stop", methods=["POST"])
def stop_addition():
    global addition_running, addition_paused
    addition_stop_event.set()
    addition_pause_event.set()  # فك أي تعليق في حال كانت موقوفة مؤقتاً
    addition_running = False
    addition_paused = False
    socketio.emit("log", {"type": "error", "message": "⏹️ تم إنهاء عملية الإضافة بواسطة المستخدم"})
    return jsonify({"success": True, "message": "تم إنهاء العملية"})


@app.route("/api/adder/status", methods=["GET"])
def get_adder_status():
    prog = load_progress()
    return jsonify({
        "running": addition_running,
        "paused": addition_paused,
        "progress": prog
    })


def run_addition_worker(accounts, group_input, members, delay_min, delay_max, max_per_account, start_index, stats):
    """العامل الخلفي المسؤول عن تنفيذ إضافة الأعضاء بالتناوب وحفظ التقدم"""
    global addition_running, addition_paused

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(
            _async_addition(accounts, group_input, members, delay_min, delay_max, max_per_account, start_index, stats, loop)
        )
    except Exception as e:
        socketio.emit("log", {"type": "error", "message": f"❌ خطأ غير متوقع في المحرك: {str(e)}"})
    finally:
        loop.close()
        addition_running = False
        addition_paused = False
        socketio.emit("addition_complete", stats)


async def _async_addition(accounts, group_input, members, delay_min, delay_max, max_per_account, start_index, stats, loop):
    socketio.emit("log", {"type": "info", "message": "🔌 جاري تهيئة الحسابات والاتصال بـ تيليجرام..."})

    active_pool = []  # list of dicts: {"client": client, "account": acc, "count": 0}

    for acc in accounts:
        if addition_stop_event.is_set():
            break
        try:
            client = TelegramClient(acc["session"], acc["api_id"], acc["api_hash"], loop=loop)
            await client.connect()
            if await client.is_user_authorized():
                active_pool.append({"client": client, "account": acc, "count": 0})
                socketio.emit("log", {
                    "type": "success",
                    "message": f"✅ متصل: {acc['phone']} ({acc.get('name', '')})"
                })
            else:
                socketio.emit("log", {
                    "type": "error",
                    "message": f"❌ جلسة منتهية للحساب: {acc['phone']}"
                })
                await client.disconnect()
        except Exception as e:
            socketio.emit("log", {
                "type": "error",
                "message": f"❌ تعذر ربط الحساب {acc['phone']}: {str(e)}"
            })

    if not active_pool:
        socketio.emit("log", {"type": "error", "message": "🛑 لا توجد أي حسابات صالحة أو متصلة للبدء!"})
        return

    # فحص والتحقق من المجموعة الهدف
    primary_client = active_pool[0]["client"]
    try:
        target_entity = await primary_client.get_entity(group_input)
        group_title = getattr(target_entity, "title", group_input)
        socketio.emit("log", {
            "type": "success",
            "message": f"🎯 تم العثور على المجموعة الهدف: {group_title} (ID: {target_entity.id})"
        })
    except Exception as e:
        socketio.emit("log", {"type": "error", "message": f"❌ تعذر العثور على المجموعة الهدف: {str(e)}"})
        for item in active_pool:
            try:
                await item["client"].disconnect()
            except Exception:
                pass
        return

    total = len(members)
    current_index = start_index
    pool_index = 0

    socketio.emit("addition_started", {
        "total": total,
        "current": current_index,
        "active_accounts": len(active_pool)
    })

    while current_index < total:
        # التحقق من الإيقاف
        if addition_stop_event.is_set():
            break

        # التحقق من الإيقاف المؤقت
        while addition_paused:
            if addition_stop_event.is_set():
                break
            await asyncio.sleep(1)

        if not active_pool:
            socketio.emit("log", {"type": "error", "message": "🛑 توقفت جميع الحسابات إما بسبب حدود تيليجرام أو انتهاء الحد الأقصى!"})
            break

        # اختيار الحساب بنظام التناوب (Round-Robin)
        account_idx = pool_index % len(active_pool)
        current_worker = active_pool[account_idx]
        worker_client = current_worker["client"]
        worker_acc = current_worker["account"]

        member_target = members[current_index]

        socketio.emit("progress", {
            "current": current_index + 1,
            "total": total,
            "member": member_target,
            "account": worker_acc.get("phone", ""),
            "stats": stats
        })

        socketio.emit("log", {
            "type": "info",
            "message": f"👤 [{current_index + 1}/{total}] محاولة إضافة @{member_target} عبر الحساب {worker_acc.get('phone')}"
        })

        advance_member = True

        try:
            # جلب كائن العضو المراد إضافته
            user_to_add = await worker_client.get_entity(member_target)

            # طلب الإضافة للمجموعة
            await worker_client(
                functions.channels.InviteToChannelRequest(
                    channel=target_entity,
                    users=[user_to_add]
                )
            )

            stats["success"] += 1
            current_worker["count"] += 1
            socketio.emit("log", {
                "type": "success",
                "message": f"   ✅ تمت إضافة @{member_target} بنجاح (حساب {worker_acc.get('phone')} أضاف {current_worker['count']}/{max_per_account})"
            })

            # فحص إذا كان الحساب وصل للحد الأقصى المحدد له
            if max_per_account > 0 and current_worker["count"] >= max_per_account:
                socketio.emit("log", {
                    "type": "warning",
                    "message": f"   🔔 الحساب {worker_acc.get('phone')} وصل للحد الأقصى المخصص له ({max_per_account} عضو). سيتم إراحته."
                })
                active_pool.remove(current_worker)
                await worker_client.disconnect()
                pool_index = 0
                advance_member = True

        except UserAlreadyParticipantError:
            stats["already_member"] += 1
            socketio.emit("log", {"type": "warning", "message": f"   ℹ️ @{member_target} موجود في المجموعة مسبقاً."})

        except UserPrivacyRestrictedError:
            stats["skipped"] += 1
            socketio.emit("log", {"type": "warning", "message": f"   ⚠️ @{member_target} خصوصية العضو تمنع إضافته للمجموعات."})

        except UserNotMutualContactError:
            stats["skipped"] += 1
            socketio.emit("log", {"type": "warning", "message": f"   ⚠️ @{member_target} يتطلب أن يكون جهة اتصال متبادلة."})

        except InputUserDeactivatedError:
            stats["skipped"] += 1
            socketio.emit("log", {"type": "warning", "message": f"   ⚠️ @{member_target} الحساب محذوف أو معطل."})

        except UsernameNotOccupiedError:
            stats["skipped"] += 1
            socketio.emit("log", {"type": "warning", "message": f"   ⚠️ @{member_target} اسم المستخدم غير صحيح أو غير موجود."})

        except UserKickedError:
            stats["skipped"] += 1
            socketio.emit("log", {"type": "warning", "message": f"   ⚠️ @{member_target} هذا العضو محظور من المجموعة."})

        except PeerFloodError:
            stats["disabled_accounts"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   🚫 الحساب {worker_acc.get('phone')} وصل لحد تيليجرام (PeerFlood)! تم استبعاده لحمايته."
            })
            active_pool.remove(current_worker)
            await worker_client.disconnect()
            advance_member = False  # نعيد محاولة إضافة العضو بحساب آخر
            pool_index = 0

        except FloodWaitError as e:
            wait_sec = e.seconds
            if wait_sec <= 60:
                socketio.emit("log", {
                    "type": "warning",
                    "message": f"   ⏳ انتظار Flood قصير ({wait_sec} ثانية)..."
                })
                await asyncio.sleep(wait_sec + 2)
                advance_member = False
            else:
                stats["disabled_accounts"] += 1
                socketio.emit("log", {
                    "type": "error",
                    "message": f"   🚫 انتظار طويل للحساب {worker_acc.get('phone')} ({wait_sec} ثانية). تم استبعاد الحساب."
                })
                active_pool.remove(current_worker)
                await worker_client.disconnect()
                advance_member = False
                pool_index = 0

        except ChatAdminRequiredError:
            stats["disabled_accounts"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   ❌ الحساب {worker_acc.get('phone')} لا يمتلك صلاحية الإشراف لإضافة أعضاء!"
            })
            active_pool.remove(current_worker)
            await worker_client.disconnect()
            advance_member = False
            pool_index = 0

        except (UserBannedInChannelError, ChannelPrivateError) as e:
            stats["disabled_accounts"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   ❌ الحساب {worker_acc.get('phone')} محظور من المجموعة أو القناة خاصة: {e}"
            })
            active_pool.remove(current_worker)
            await worker_client.disconnect()
            advance_member = False
            pool_index = 0

        except Exception as e:
            stats["failed"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   ❌ فشل في إضافة @{member_target}: {str(e)}"
            })

        if advance_member:
            current_index += 1
            pool_index += 1

        # حفظ التقدم باستمرار
        save_progress({
            "current_index": current_index,
            "total": total,
            "target_group": group_input,
            "stats": stats,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        # فترة التأخير العشوائي
        if current_index < total and active_pool and not addition_stop_event.is_set():
            actual_delay = random.randint(min(delay_min, delay_max), max(delay_min, delay_max))
            socketio.emit("progress", {
                "current": current_index,
                "total": total,
                "stats": stats,
                "waiting": True,
                "delay": actual_delay
            })
            for _ in range(actual_delay):
                if addition_stop_event.is_set():
                    break
                await asyncio.sleep(1)

    # إغلاق اتصالات الحسابات
    for item in active_pool:
        try:
            await item["client"].disconnect()
        except Exception:
            pass

    # تقرير النهاية
    socketio.emit("log", {"type": "info", "message": "═" * 45})
    socketio.emit("log", {"type": "info", "message": "📊 التقرير النهائي للعملية:"})
    socketio.emit("log", {"type": "success", "message": f"   ✅ تمت إضافتهم بنجاح: {stats['success']}"})
    socketio.emit("log", {"type": "warning", "message": f"   ℹ️ أعضاء سابقين: {stats.get('already_member', 0)}"})
    socketio.emit("log", {"type": "warning", "message": f"   ⚠️ تم تخطيهم (خصوصية/تعطيل): {stats['skipped']}"})
    socketio.emit("log", {"type": "error", "message": f"   ❌ فشل: {stats['failed']}"})
    socketio.emit("log", {"type": "error", "message": f"   🚫 حسابات تم تقييدها أو عزلها: {stats['disabled_accounts']}"})
    socketio.emit("log", {"type": "info", "message": "═" * 45})


# ================== تشغيل التطبيق ==================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "═" * 60)
    print("  🚀 نظام إضافة وسحب أعضاء تيليجرام المتكامل (Pro Suite)")
    print(f"  🌐 رابط الواجهة: http://127.0.0.1:{port}")
    print("═" * 60 + "\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
