# -*- coding: utf-8 -*-
"""
سيرفر إضافة أعضاء تيليجرام
Flask + Telethon + SocketIO
"""

import os
import json
import asyncio
import threading
import time
from flask import Flask, render_template, request, jsonify
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
    UserNotParticipantError
)

# ================== الإعداد ==================
app = Flask(__name__)
app.config["SECRET_KEY"] = "telegram-adder-secret-key-2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
MEMBERS_FILE = os.path.join(BASE_DIR, "members.txt")

# متغيرات النظام
addition_running = False
addition_thread = None
stop_event = threading.Event()


# ================== التهيئة ==================
def init():
    if not os.path.exists(SESSIONS_DIR):
        os.makedirs(SESSIONS_DIR)
    if not os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
    if not os.path.exists(MEMBERS_FILE):
        with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
            f.write("")


def load_accounts():
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


# ================== المسارات (Routes) ==================
@app.route("/")
def index():
    return render_template("index.html")


# ---------- إدارة الحسابات ----------

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    accounts = load_accounts()
    result = []
    for acc in accounts:
        session_file = acc["session"] + ".session"
        active = os.path.exists(session_file)
        result.append({
            "id": acc.get("id", 0),
            "name": acc.get("name", ""),
            "username": acc.get("username", ""),
            "phone": acc.get("phone", ""),
            "active": active
        })
    return jsonify(result)


@app.route("/api/send_code", methods=["POST"])
def send_verification_code():
    """إرسال رمز التحقق"""
    data = request.json
    api_id = data.get("api_id")
    api_hash = data.get("api_hash")
    phone = data.get("phone")

    if not all([api_id, api_hash, phone]):
        return jsonify({"success": False, "message": "جميع الحقول مطلوبة"}), 400

    try:
        session_name = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
        client = TelegramClient(session_name, int(api_id), api_hash)
        client.connect()

        if client.is_user_authorized():
            client.disconnect()
            return jsonify({"success": False, "message": "هذا الحساب مسجل مسبقاً"}), 400

        client.send_code_request(phone)

        # حفظ مؤقت للبيانات
        temp_data = {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "session": session_name
        }
        temp_file = os.path.join(SESSIONS_DIR, f"temp_{phone.replace('+', '')}.json")
        with open(temp_file, "w") as f:
            json.dump(temp_data, f)

        client.disconnect()
        return jsonify({"success": True, "message": "تم إرسال رمز التحقق"})

    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ: {str(e)}"}), 400


@app.route("/api/verify_code", methods=["POST"])
def verify_code():
    """التحقق من الرمز وتسجيل الحساب"""
    data = request.json
    phone = data.get("phone")
    code = data.get("code")
    password = data.get("password", "")

    temp_file = os.path.join(SESSIONS_DIR, f"temp_{phone.replace('+', '')}.json")
    if not os.path.exists(temp_file):
        return jsonify({"success": False, "message": "انتهت صلاحية الجلسة، أعد المحاولة"}), 400

    with open(temp_file, "r") as f:
        temp_data = json.load(f)

    try:
        client = TelegramClient(
            temp_data["session"],
            int(temp_data["api_id"]),
            temp_data["api_hash"]
        )
        client.connect()

        try:
            client.sign_in(phone=phone, code=code)
        except Exception:
            if password:
                client.sign_in(password=password)
            else:
                client.disconnect()
                return jsonify({
                    "success": False,
                    "needs_password": True,
                    "message": "تحتاج كلمة مرور التحقق بخطوتين"
                }), 400

        me = client.get_me()
        client.disconnect()

        # حفظ الحساب
        accounts = load_accounts()
        account_id = len(accounts) + 1

        new_account = {
            "id": account_id,
            "phone": phone,
            "api_id": int(temp_data["api_id"]),
            "api_hash": temp_data["api_hash"],
            "session": temp_data["session"],
            "name": me.first_name or "",
            "username": me.username or ""
        }
        accounts.append(new_account)
        save_accounts(accounts)

        # حذف الملف المؤقت
        os.remove(temp_file)

        return jsonify({
            "success": True,
            "message": f"تم إضافة الحساب: {new_account['name']}",
            "account": {
                "name": new_account["name"],
                "username": "@" + new_account["username"] if new_account["username"] else "",
                "phone": phone
            }
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ: {str(e)}"}), 400


@app.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account(acc_id):
    accounts = load_accounts()
    new_accounts = [a for a in accounts if a.get("id") != acc_id]

    if len(new_accounts) == len(accounts):
        return jsonify({"success": False, "message": "الحساب غير موجود"}), 404

    # حذف ملف الجلسة
    for acc in accounts:
        if acc.get("id") == acc_id:
            session_file = acc["session"] + ".session"
            if os.path.exists(session_file):
                os.remove(session_file)

    save_accounts(new_accounts)
    return jsonify({"success": True, "message": "تم حذف الحساب"})


# ---------- إضافة الأعضاء ----------

@app.route("/api/start_addition", methods=["POST"])
def start_addition():
    global addition_running, addition_thread, stop_event

    if addition_running:
        return jsonify({"success": False, "message": "العملية جارية بالفعل"}), 400

    data = request.json
    group_input = data.get("group_id", "").strip()
    delay = int(data.get("delay", 10))
    members_text = data.get("members", "")
    source = data.get("source", "text")  # "text" أو "file"

    # قراءة الأعضاء
    if source == "file":
        with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
            members = [
                line.strip().lstrip("@").strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
    else:
        members = [
            line.strip().lstrip("@").strip()
            for line in members_text.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]

    if not members:
        return jsonify({"success": False, "message": "قائمة الأعضاء فارغة"}), 400

    if not group_input:
        return jsonify({"success": False, "message": "أدخل معرف المجموعة"}), 400

    accounts = load_accounts()
    if not accounts:
        return jsonify({"success": False, "message": "لا توجد حسابات مسجلة"}), 400

    # بدء العملية في Thread منفصل
    addition_running = True
    stop_event.clear()

    addition_thread = threading.Thread(
        target=run_addition_sync,
        args=(accounts, group_input, members, delay)
    )
    addition_thread.start()

    return jsonify({
        "success": True,
        "message": "تم بدء العملية",
        "total_members": len(members),
        "total_accounts": len(accounts)
    })


@app.route("/api/stop_addition", methods=["POST"])
def stop_addition():
    global addition_running
    stop_event.set()
    addition_running = False
    socketio.emit("log", {"type": "info", "message": "⏹️ تم إيقاف العملية بواسطة المستخدم"})
    return jsonify({"success": True, "message": "تم إيقاف العملية"})


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({"running": addition_running})


@app.route("/api/save_members", methods=["POST"])
def save_members():
    data = request.json
    members_text = data.get("members", "")
    with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
        f.write(members_text)
    return jsonify({"success": True, "message": "تم الحفظ"})


@app.route("/api/load_members", methods=["GET"])
def load_members():
    if os.path.exists(MEMBERS_FILE):
        with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
            return jsonify({"members": f.read()})
    return jsonify({"members": ""})


# ================== منطق الإضافة (Thread) ==================
def run_addition_sync(accounts, group_input, members, delay):
    """تشغيل عملية الإضافة في Thread منفصل"""
    global addition_running

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(
            run_addition_async(accounts, group_input, members, delay)
        )
    except Exception as e:
        socketio.emit("log", {"type": "error", "message": f"❌ خطأ عام: {str(e)}"})
    finally:
        loop.close()
        addition_running = False
        socketio.emit("addition_complete")


async def run_addition_async(accounts, group_input, members, delay):
    """المنطق الأساسي لإضافة الأعضاء"""

    # ===== الاتصال بالحسابات =====
    socketio.emit("log", {"type": "info", "message": "🔌 جاري الاتصال بالحسابات..."})
    active_clients = []  # [(client, account_info)]

    for acc in accounts:
        if stop_event.is_set():
            break
        try:
            client = TelegramClient(acc["session"], acc["api_id"], acc["api_hash"])
            await client.connect()

            if await client.is_user_authorized():
                active_clients.append((client, acc))
                socketio.emit("log", {
                    "type": "success",
                    "message": f"✅ متصل: {acc['phone']} ({acc.get('name', '')})"
                })
            else:
                socketio.emit("log", {
                    "type": "error",
                    "message": f"❌ جلسة منتهية: {acc['phone']}"
                })
                await client.disconnect()
        except Exception as e:
            socketio.emit("log", {
                "type": "error",
                "message": f"❌ فشل الاتصال: {acc['phone']} - {e}"
            })

    if not active_clients:
        socketio.emit("log", {"type": "error", "message": "❌ لا توجد حسابات فعالة!"})
        return

    # ===== الحصول على المجموعة =====
    try:
        entity = await active_clients[0][0].get_entity(group_input)
        socketio.emit("log", {
            "type": "success",
            "message": f"✅ المجموعة المستهدفة: {entity.title} (ID: {entity.id})"
        })
    except Exception as e:
        socketio.emit("log", {
            "type": "error",
            "message": f"❌ خطأ في المجموعة: {e}"
        })
        for c, _ in active_clients:
            await c.disconnect()
        return

    # ===== إحصائيات =====
    stats = {"success": 0, "failed": 0, "skipped": 0, "disabled_accounts": 0}
    total = len(members)
    member_index = 0
    account_idx = 0
    fail_count = {}

    socketio.emit("addition_started", {
        "total": total,
        "accounts": len(active_clients)
    })
    socketio.emit("log", {
        "type": "info",
        "message": f"🚀 بدء الإضافة: {total} عضو باستخدام {len(active_clients)} حساب (تأخير: {delay}ث)"
    })

    # ===== الحلقة الرئيسية (Round-Robin) =====
    while member_index < total:
        if stop_event.is_set():
            break

        if not active_clients:
            socketio.emit("log", {"type": "error", "message": "🛑 جميع الحسابات توقفت!"})
            break

        # اختيار الحساب الحالي (Round-Robin)
        real_idx = account_idx % len(active_clients)
        client, acc_info = active_clients[real_idx]
        member = members[member_index]

        socketio.emit("progress", {
            "current": member_index + 1,
            "total": total,
            "member": member,
            "account": acc_info.get("phone", ""),
            "stats": stats
        })

        socketio.emit("log", {
            "type": "info",
            "message": f"👤 [{member_index + 1}/{total}] إضافة @{member} ← الحساب #{real_idx + 1} ({acc_info.get('phone', '')})"
        })

        try:
            # البحث عن المستخدم وإضافته
            user_entity = await client.get_entity(member)

            await client(
                functions.channels.InviteToChannelRequest(
                    channel=entity,
                    users=[user_entity]
                )
            )

            stats["success"] += 1
            fail_count[member] = 0
            socketio.emit("log", {
                "type": "success",
                "message": f"   ✅ تمت إضافة @{member} بنجاح"
            })
            member_index += 1

        except UserPrivacyRestrictedError:
            stats["skipped"] += 1
            socketio.emit("log", {
                "type": "warning",
                "message": f"   ⚠️ @{member} - إعدادات الخصوصية تمنع الإضافة"
            })
            member_index += 1

        except UserNotMutualContactError:
            stats["skipped"] += 1
            socketio.emit("log", {
                "type": "warning",
                "message": f"   ⚠️ @{member} - ليس جهة اتصال متبادلة"
            })
            member_index += 1

        except InputUserDeactivatedError:
            stats["skipped"] += 1
            socketio.emit("log", {
                "type": "warning",
                "message": f"   ⚠️ @{member} - حساب محذوف/معطل"
            })
            member_index += 1

        except UsernameNotOccupiedError:
            stats["skipped"] += 1
            socketio.emit("log", {
                "type": "warning",
                "message": f"   ⚠️ @{member} - اسم المستخدم غير موجود"
            })
            member_index += 1

        except UserKickedError:
            stats["skipped"] += 1
            socketio.emit("log", {
                "type": "warning",
                "message": f"   ⚠️ @{member} - محظور من المجموعة"
            })
            member_index += 1

        except PeerFloodError:
            stats["disabled_accounts"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   🚫 تجاوز الحد! تعطيل الحساب #{real_idx + 1} ({acc_info.get('phone')})"
            })
            active_clients.remove((client, acc_info))
            await client.disconnect()
            account_idx = 0
            continue

        except FloodWaitError as e:
            wait = e.seconds
            if wait <= 300:
                socketio.emit("log", {
                    "type": "warning",
                    "message": f"   ⏳ انتظار {wait} ثانية..."
                })
                await asyncio.sleep(wait + 5)
                continue
            else:
                stats["disabled_accounts"] += 1
                socketio.emit("log", {
                    "type": "error",
                    "message": f"   🚫 انتظار طويل ({wait}ث)! تعطيل الحساب #{real_idx + 1}"
                })
                active_clients.remove((client, acc_info))
                await client.disconnect()
                account_idx = 0
                continue

        except ChatAdminRequiredError:
            stats["disabled_accounts"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   ❌ الحساب ليس مشرفاً! تعطيل #{real_idx + 1}"
            })
            active_clients.remove((client, acc_info))
            await client.disconnect()
            account_idx = 0
            continue

        except UserBannedInChannelError:
            stats["disabled_accounts"] += 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   ❌ الحساب محظور من المجموعة! تعطيل #{real_idx + 1}"
            })
            active_clients.remove((client, acc_info))
            await client.disconnect()
            account_idx = 0
            continue

        except Exception as e:
            fail_count[member] = fail_count.get(member, 0) + 1
            socketio.emit("log", {
                "type": "error",
                "message": f"   ❌ خطأ: {e} (محاولة {fail_count[member]}/3)"
            })

            if fail_count[member] >= 3:
                stats["failed"] += 1
                member_index += 1

        # الانتقال للحساب التالي
        account_idx += 1

        # التأخير
        socketio.emit("progress", {
            "current": member_index,
            "total": total,
            "member": member,
            "account": acc_info.get("phone", ""),
            "stats": stats,
            "waiting": True
        })

        for _ in range(delay):
            if stop_event.is_set():
                break
            await asyncio.sleep(1)

    # قطع الاتصالات
    for c, _ in active_clients:
        try:
            await c.disconnect()
        except Exception:
            pass

    # النتائج النهائية
    socketio.emit("log", {"type": "info", "message": "═" * 40})
    socketio.emit("log", {"type": "info", "message": "📊 النتائج النهائية:"})
    socketio.emit("log", {"type": "success", "message": f"   ✅ نجح: {stats['success']}"})
    socketio.emit("log", {"type": "error", "message": f"   ❌ فشل: {stats['failed']}"})
    socketio.emit("log", {"type": "warning", "message": f"   ⚠️ تخطي: {stats['skipped']}"})
    socketio.emit("log", {"type": "error", "message": f"   🚫 حسابات معطلة: {stats['disabled_accounts']}"})
    socketio.emit("addition_complete", stats)


# ================== تشغيل ==================
if __name__ == "__main__":
    init()
    print("\n" + "═" * 55)
    print("  🤖 أداة إضافة أعضاء تيليجرام - واجهة ويب")
    print("  🌐 افتح المتصفح على: http://127.0.0.1:5000")
    print("═" * 55 + "\n")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)