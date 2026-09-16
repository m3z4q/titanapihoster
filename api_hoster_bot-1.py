import os
import re
import json
import time
import sqlite3
import threading
from pathlib import Path
from urllib.parse import quote

import requests
import telebot
from telebot import types

# ============================================================
# TITAN API HOSTER BOT
# Only these two values are required in this file.
# ============================================================
BOT_TOKEN = "8597162496:AAE_3tsHg_Cn-t31czK-zGT7_LGbO6hVAbM"
ADMIN_ID = 8794321786

# Force-sub channel. Set to "" to disable force-sub.
FORCE_JOIN_CHANNEL = "@aurexkeng"

# Public base URL used for generated API packages.
# Example: https://your-render-service.onrender.com
HOSTER_BASE_URL = "https://titanapihoster.onrender.com"

# Every hosted API URL is pinged every 5 minutes.
KEEP_ALIVE_SECONDS = 300

DATA_DIR = Path("titan_api_hoster_data")
APPS_DIR = DATA_DIR / "apps"
DB_PATH = DATA_DIR / "bot.db"
DATA_DIR.mkdir(exist_ok=True)
APPS_DIR.mkdir(exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# -------------------- DATABASE --------------------
db_lock = threading.Lock()

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

with db_lock:
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        joined_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS apps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        app_name TEXT NOT NULL,
        slug TEXT NOT NULL,
        app_file TEXT NOT NULL,
        requirements_file TEXT,
        url TEXT NOT NULL,
        created_at INTEGER,
        last_ping INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1
    );
    """)
    conn.commit()
    conn.close()

# -------------------- HELPERS --------------------
def save_user(user):
    with db_lock:
        conn = db()
        old = conn.execute(
            "SELECT user_id FROM users WHERE user_id=?", (user.id,)
        ).fetchone()
        conn.execute(
            """INSERT INTO users(user_id, first_name, username, joined_at)
               VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
               first_name=excluded.first_name,
               username=excluded.username""",
            (user.id, user.first_name or "", user.username or "", int(time.time()))
        )
        conn.commit()
        conn.close()
    return old is None

def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s[:40] or "api"

def unique_slug(name):
    base = slugify(name)
    slug = base
    n = 2
    with db_lock:
        conn = db()
        while conn.execute("SELECT 1 FROM apps WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{n}"
            n += 1
        conn.close()
    return slug

def safe_filename(name):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)

def join_required(user_id):
    if not FORCE_JOIN_CHANNEL or FORCE_JOIN_CHANNEL.startswith("@YOUR_"):
        return False
    try:
        m = bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return m.status in ("creator", "administrator", "member")
    except Exception:
        return True

def join_markup():
    kb = types.InlineKeyboardMarkup()
    channel = FORCE_JOIN_CHANNEL.lstrip("@")
    kb.add(types.InlineKeyboardButton(
        "📢 JOIN CHANNEL",
        url=f"https://t.me/{channel}"
    ))
    kb.add(types.InlineKeyboardButton("✅ CHECK JOIN", callback_data="check_join"))
    return kb

def home_markup(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📤 Upload API", callback_data="upload"),
        types.InlineKeyboardButton("📋 My APIs", callback_data="myapis"),
    )
    kb.add(
        types.InlineKeyboardButton("📖 Help", callback_data="help"),
        types.InlineKeyboardButton("ℹ️ About", callback_data="about"),
    )
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("👑 Admin Panel", callback_data="admin"))
    return kb

def admin_markup():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Users", callback_data="admin_users"),
        types.InlineKeyboardButton("🚀 APIs", callback_data="admin_apis"),
    )
    kb.add(
        types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
    )
    return kb

def send_home(chat_id):
    bot.send_message(
        chat_id,
        "🚀 <b>TITAN API HOSTER</b>\n\n"
        "📤 Upload your <code>app.py</code>\n"
        "📦 Add <code>requirements.txt</code> (optional)\n"
        "✍️ Set your API name\n"
        "🌐 Get your API URL\n\n"
        "♻️ Hosted APIs are checked every 5 minutes.",
        reply_markup=home_markup(chat_id)
    )

# -------------------- STATE --------------------
states = {}

def set_state(user_id, state, data=None):
    states[user_id] = {"state": state, "data": data or {}}

def clear_state(user_id):
    states.pop(user_id, None)

# -------------------- START / JOIN --------------------
@bot.message_handler(commands=["start"])
def start(message):
    is_new = save_user(message.from_user)

    if is_new and not is_admin(message.from_user.id):
        notify_admin_new_user(message.from_user)

    if join_required(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "🔒 <b>JOIN REQUIRED</b>\n\n"
            "Please join our channel first, then press CHECK JOIN.",
            reply_markup=join_markup()
        )
        return

    send_home(message.chat.id)

@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def check_join(call):
    if join_required(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Please join the channel first.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "✅ Verified!")
    bot.send_message(call.message.chat.id, "✅ <b>Verified successfully!</b>")
    send_home(call.message.chat.id)

def notify_admin_new_user(user):
    try:
        username = f"@{user.username}" if user.username else "No username"
        bot.send_message(
            ADMIN_ID,
            "🎉 <b>NEW USER JOINED</b>\n\n"
            f"👤 Name: {user.first_name or 'Unknown'}\n"
            f"🔗 Username: {username}\n"
            f"🆔 User ID: <code>{user.id}</code>"
        )
    except Exception:
        pass

# -------------------- MENU CALLBACKS --------------------
@bot.callback_query_handler(func=lambda c: c.data == "upload")
def upload_start(call):
    if join_required(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Join the channel first.", show_alert=True)
        return
    set_state(call.from_user.id, "waiting_app")
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "📤 <b>Send your app.py</b>\n\n"
        "Only Python <code>.py</code> file is accepted."
    )

@bot.callback_query_handler(func=lambda c: c.data == "myapis")
def my_apis(call):
    show_my_apis(call.message.chat.id, call.from_user.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "help")
def help_cb(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "📖 <b>How to use</b>\n\n"
        "1. Press 📤 Upload API\n"
        "2. Send <code>app.py</code>\n"
        "3. Optionally send <code>requirements.txt</code>\n"
        "4. Enter an app name\n"
        "5. The bot prepares a Render-ready API package.\n\n"
        "⚠️ Your app must expose a web server on the Render PORT."
    )

@bot.callback_query_handler(func=lambda c: c.data == "about")
def about_cb(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "⚡ <b>TITAN API HOSTER</b>\n\n"
        "📡 Render-ready API deployment helper\n"
        "♻️ 5-minute health pings\n"
        "🔒 Force-sub support\n"
        "👑 Admin broadcast & statistics"
    )

# -------------------- FILE UPLOAD --------------------
@bot.message_handler(content_types=["document"])
def document_handler(message):
    user_id = message.from_user.id
    save_user(message.from_user)

    if join_required(user_id):
        bot.send_message(message.chat.id, "🔒 Join the required channel first.", reply_markup=join_markup())
        return

    state = states.get(user_id, {})
    state_name = state.get("state")

    filename = message.document.file_name or ""

    if state_name == "waiting_app":
        if not filename.lower().endswith(".py"):
            bot.reply_to(message, "❌ Please send a valid <code>app.py</code> / Python .py file.")
            return

        info = bot.get_file(message.document.file_id)
        content = bot.download_file(info.file_path)

        user_dir = APPS_DIR / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        app_path = user_dir / "app.py"
        app_path.write_bytes(content)

        set_state(user_id, "waiting_requirements", {
            "app_path": str(app_path),
            "requirements_path": None
        })

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏭️ Skip requirements.txt", callback_data="skip_requirements"))
        bot.send_message(
            message.chat.id,
            "✅ <b>app.py received!</b>\n\n"
            "Now send <code>requirements.txt</code> or press SKIP.",
            reply_markup=kb
        )
        return

    if state_name == "waiting_requirements":
        if filename.lower() != "requirements.txt":
            bot.reply_to(message, "❌ Please send <code>requirements.txt</code> or press SKIP.")
            return

        info = bot.get_file(message.document.file_id)
        content = bot.download_file(info.file_path)

        app_path = Path(state["data"]["app_path"])
        req_path = app_path.parent / "requirements.txt"
        req_path.write_bytes(content)

        set_state(user_id, "waiting_name", {
            "app_path": str(app_path),
            "requirements_path": str(req_path)
        })
        bot.send_message(message.chat.id, "✍️ <b>Send your API/app name:</b>")
        return

    bot.send_message(message.chat.id, "ℹ️ Use 📤 Upload API from the menu first.")

@bot.callback_query_handler(func=lambda c: c.data == "skip_requirements")
def skip_requirements(call):
    user_id = call.from_user.id
    state = states.get(user_id, {})
    if state.get("state") != "waiting_requirements":
        bot.answer_callback_query(call.id, "No upload is waiting.", show_alert=True)
        return

    set_state(user_id, "waiting_name", {
        "app_path": state["data"]["app_path"],
        "requirements_path": None
    })
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "✍️ <b>Send your API/app name:</b>")

@bot.message_handler(func=lambda m: states.get(m.from_user.id, {}).get("state") == "waiting_name")
def name_handler(message):
    user_id = message.from_user.id
    name = (message.text or "").strip()

    if not name or len(name) > 60:
        bot.reply_to(message, "❌ Enter a valid name between 1 and 60 characters.")
        return

    data = states[user_id]["data"]
    slug = unique_slug(name)

    app_path = Path(data["app_path"])
    req_path = data.get("requirements_path")

    # Create a Render-ready start file.
    render_yaml = app_path.parent / "render.yaml"
    render_yaml.write_text(
        "services:\n"
        f"  - type: web\n"
        f"    name: {slug}\n"
        "    runtime: python\n"
        "    buildCommand: pip install -r requirements.txt\n"
        "    startCommand: python app.py\n"
        "    plan: free\n",
        encoding="utf-8"
    )

    # Create requirements if user skipped it.
    if not req_path:
        req_path = str(app_path.parent / "requirements.txt")
        Path(req_path).write_text(
            "flask\n"
            "fastapi\n"
            "uvicorn\n"
            "requests\n"
            "gunicorn\n",
            encoding="utf-8"
        )

    url = f"{HOSTER_BASE_URL.rstrip('/')}/{quote(slug)}"

    with db_lock:
        conn = db()
        cur = conn.execute(
            """INSERT INTO apps
               (user_id, app_name, slug, app_file, requirements_file, url, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (user_id, name, slug, str(app_path), str(req_path), url, int(time.time()))
        )
        app_id = cur.lastrowid
        conn.commit()
        conn.close()

    clear_state(user_id)

    bot.send_message(
        message.chat.id,
        "🎉 <b>API PACKAGE READY!</b>\n\n"
        f"📱 Name: <b>{name}</b>\n"
        f"🆔 ID: <code>{app_id}</code>\n"
        f"🔗 Slug: <code>{slug}</code>\n\n"
        "📦 Your Render-ready project has been prepared.\n"
        "🚀 Deploy this project on Render as a Web Service.\n\n"
        f"🌐 Suggested URL:\n<code>{url}</code>\n\n"
        "♻️ Keep-alive worker will ping registered URLs every 5 minutes."
    )

    if is_admin(user_id):
        return

# -------------------- API LIST --------------------
def show_my_apis(chat_id, user_id):
    with db_lock:
        conn = db()
        rows = conn.execute(
            "SELECT * FROM apps WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()
        conn.close()

    if not rows:
        bot.send_message(chat_id, "📭 <b>No APIs found.</b>")
        return

    text = "📋 <b>MY APIs</b>\n\n"
    for row in rows:
        status = "🟢 Active" if row["active"] else "🔴 Inactive"
        text += (
            f"#{row['id']} • <b>{row['app_name']}</b>\n"
            f"🔗 <code>{row['url']}</code>\n"
            f"{status}\n\n"
        )
    bot.send_message(chat_id, text)

# -------------------- ADMIN --------------------
@bot.message_handler(commands=["admin"])
def admin_cmd(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "👑 <b>ADMIN PANEL</b>", reply_markup=admin_markup())

@bot.callback_query_handler(func=lambda c: c.data == "admin")
def admin_cb(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Admin only.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "👑 <b>ADMIN PANEL</b>", reply_markup=admin_markup())

@bot.callback_query_handler(func=lambda c: c.data == "admin_users")
def admin_users(call):
    if not is_admin(call.from_user.id):
        return
    with db_lock:
        conn = db()
        count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        conn.close()
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"👥 <b>Total Users:</b> {count}")

@bot.callback_query_handler(func=lambda c: c.data == "admin_apis")
def admin_apis(call):
    if not is_admin(call.from_user.id):
        return
    with db_lock:
        conn = db()
        total = conn.execute("SELECT COUNT(*) c FROM apps").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) c FROM apps WHERE active=1").fetchone()["c"]
        conn.close()
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"🚀 <b>Total APIs:</b> {total}\n"
        f"🟢 <b>Active:</b> {active}"
    )

@bot.callback_query_handler(func=lambda c: c.data == "admin_broadcast")
def admin_broadcast_cb(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Admin only.", show_alert=True)
        return
    set_state(call.from_user.id, "broadcast")
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📢 <b>Send the broadcast message now.</b>")

@bot.message_handler(commands=["broadcast"])
def broadcast_cmd(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Admin only.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        set_state(message.from_user.id, "broadcast")
        bot.reply_to(message, "📢 Send the broadcast message.")
        return
    do_broadcast(parts[1], message.chat.id)

@bot.message_handler(func=lambda m: states.get(m.from_user.id, {}).get("state") == "broadcast")
def broadcast_handler(message):
    if not is_admin(message.from_user.id):
        clear_state(message.from_user.id)
        return
    clear_state(message.from_user.id)
    do_broadcast(message.text or "", message.chat.id)

def do_broadcast(text, admin_chat_id):
    with db_lock:
        conn = db()
        users = [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]
        conn.close()

    sent = 0
    failed = 0
    for uid in users:
        try:
            bot.send_message(uid, text)
            sent += 1
            time.sleep(0.04)
        except Exception:
            failed += 1

    bot.send_message(
        admin_chat_id,
        "📢 <b>BROADCAST COMPLETE</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>"
    )

# -------------------- KEEP ALIVE --------------------
def keep_alive_loop():
    while True:
        try:
            with db_lock:
                conn = db()
                rows = conn.execute(
                    "SELECT id, url FROM apps WHERE active=1"
                ).fetchall()
                conn.close()

            for row in rows:
                url = row["url"]
                # Only ping valid HTTP(S) URLs.
                if not url.startswith(("http://", "https://")):
                    continue
                try:
                    requests.get(
                        url,
                        timeout=15,
                        headers={"User-Agent": "Titan-API-Hoster/1.0"}
                    )
                    with db_lock:
                        conn = db()
                        conn.execute(
                            "UPDATE apps SET last_ping=? WHERE id=?",
                            (int(time.time()), row["id"])
                        )
                        conn.commit()
                        conn.close()
                except Exception:
                    pass
                time.sleep(0.5)
        except Exception:
            pass

        time.sleep(KEEP_ALIVE_SECONDS)

threading.Thread(target=keep_alive_loop, daemon=True).start()

# -------------------- RUN --------------------
if __name__ == "__main__":
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Set BOT_TOKEN in api_hoster_bot.py first.")
    print("🚀 TITAN API HOSTER BOT STARTED")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
