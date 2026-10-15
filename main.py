import asyncio
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, FSInputFile
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from dotenv import load_dotenv

load_dotenv()

# Token configuration (FadeHost TELEGRAM_BOT_TOKEN ya BOT_TOKEN dono support karega)
raw_token = os.getenv("BOT_TOKEN", "").strip()
fallback_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not raw_token or raw_token == "aapka_telegram_token":
    BOT_TOKEN = fallback_token
else:
    BOT_TOKEN = raw_token

raw_owner_id = os.getenv("OWNER_ID", "0").strip()
try:
    OWNER_ID = int(raw_owner_id) if raw_owner_id else 0
except ValueError:
    OWNER_ID = 0

OFFICIAL_CHANNEL = os.getenv("OFFICIAL_CHANNEL", "").strip()
OFFICIAL_CHANNEL_URL = os.getenv("OFFICIAL_CHANNEL_URL", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip()
BOT_TITLE = os.getenv("BOT_TITLE", "Personal Update Bot").strip()
DB_PATH = Path(os.getenv("DB_PATH", "data/bot.sqlite3"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))
BACKUP_EVERY_HOURS = max(0, int(os.getenv("BACKUP_EVERY_HOURS", "24") or 24))

if not BOT_TOKEN:
    raise SystemExit("Error: BOT_TOKEN / TELEGRAM_BOT_TOKEN missing hai!")
if not OWNER_ID:
    raise SystemExit("Error: OWNER_ID missing ya invalid hai!")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("personal-bot")

# --- DATABASE SETUP ---
def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
          started_at TEXT NOT NULL, last_seen TEXT NOT NULL, blocked INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS updates(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          description TEXT DEFAULT '', url TEXT NOT NULL, active INTEGER DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS buttons(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          url TEXT NOT NULL, active INTEGER DEFAULT 1, position INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS logs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
          action TEXT NOT NULL, detail TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS broadcasts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
          source_chat TEXT, source_message_id INTEGER, total INTEGER DEFAULT 0,
          sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, status TEXT DEFAULT 'created'
        );
        """)
        defaults = {
            "welcome": f"👋 <b>{BOT_TITLE}</b> me aapka swagat hai!\n\nNeeche diye gaye menu se chunav karein:",
            "maintenance": "0",
            "official_channel": OFFICIAL_CHANNEL,
            "official_channel_url": OFFICIAL_CHANNEL_URL,
            "owner_username": OWNER_USERNAME,
            "youtube_url": os.getenv("YOUTUBE_URL", "").strip(),
            "instagram_url": os.getenv("INSTAGRAM_URL", "").strip(),
            "support_url": os.getenv("SUPPORT_URL", "").strip(),
        }
        for key, val in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, val))

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def setting(key, default=""):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with connect() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        c.execute("INSERT INTO logs(at,action,detail) VALUES(?,?,?)", (now(), "setting", key))

def log_action(action, detail=""):
    with connect() as c:
        c.execute("INSERT INTO logs(at,action,detail) VALUES(?,?,?)", (now(), action, detail[:1000]))

def is_owner(user_id):
    return int(user_id) == OWNER_ID

def save_user(user):
    with connect() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name,started_at,last_seen,blocked)
        VALUES(?,?,?,?,?,0) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen""",
        (user.id, user.username or "", user.first_name or "", now(), now()))

# --- STRICT FORCE SUBSCRIBE SYSTEM ---
def get_channel_info():
    ch_id = setting("official_channel", OFFICIAL_CHANNEL).strip()
    ch_url = setting("official_channel_url", OFFICIAL_CHANNEL_URL).strip()
    if not ch_url and ch_id.startswith("@"):
        ch_url = f"https://t.me/{ch_id[1:]}"
    return ch_id, ch_url

async def is_subscribed(bot: Bot, user_id: int):
    if is_owner(user_id):
        return True, ""
    ch_id, ch_url = get_channel_info()
    if not ch_id:
        # Agar channel configured hi nahi hai toh bypass karein
        return True, ""
    try:
        member = await bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return True, ch_url
        return False, ch_url
    except Exception as e:
        log.warning("Subscription verification error: %s", e)
        # Check fail hone par user ko lock rakhein
        return False, ch_url

def subscribe_lock_kb(channel_url: str):
    rows = []
    if channel_url:
        rows.append([InlineKeyboardButton(text="📢 Join Official Channel", url=channel_url)])
    else:
        rows.append([InlineKeyboardButton(text="📢 Join Official Channel", url="https://t.me/")])
    rows.append([InlineKeyboardButton(text="✅ I've Joined / Verify", callback_data="verify_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- MENUS ---
def build_main_menu(user_id: int):
    _, ch_url = get_channel_info()
    rows = []

    # 1. Sabse UPAR Official Channel
    if ch_url:
        rows.append([InlineKeyboardButton(text="📢 Official Channel", url=ch_url)])

    # 2. Downloads & Updates Button
    rows.append([InlineKeyboardButton(text="📥 New Update Download", callback_data="updates:0")])

    # 3. Dynamic Custom Buttons (Database se)
    with connect() as c:
        buttons = c.execute("SELECT id,title,url FROM buttons WHERE active=1 ORDER BY position,id").fetchall()
    for b in buttons:
        rows.append([InlineKeyboardButton(text=b["title"][:64], url=b["url"])])

    # 4. Social Links (Agar configure kiye hon)
    yt = setting("youtube_url")
    ig = setting("instagram_url")
    sup = setting("support_url")
    if yt:
        rows.append([InlineKeyboardButton(text="▶️ YouTube", url=yt)])
    if ig:
        rows.append([InlineKeyboardButton(text="📸 Instagram", url=ig)])
    if sup:
        rows.append([InlineKeyboardButton(text="🆘 Support", url=sup)])

    # 5. About Bot & Owner Controls
    bottom_row = [InlineKeyboardButton(text="ℹ️ About / All Info", callback_data="about_bot")]
    if is_owner(user_id):
        bottom_row.append(InlineKeyboardButton(text="👑 Owner Panel", callback_data="admin:open"))
    rows.append(bottom_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_dashboard_kb():
    rows = [
        [InlineKeyboardButton(text="📊 Bot Statistics", callback_data="admin:stats"),
         InlineKeyboardButton(text="📥 Manage Updates", callback_data="admin:updates")],
        [InlineKeyboardButton(text="➕ Add Update Link", callback_data="admin:add_update_help"),
         InlineKeyboardButton(text="➕ Add Custom Button", callback_data="admin:add_button_help")],
        [InlineKeyboardButton(text="📢 Channel Setup Help", callback_data="admin:channel_help"),
         InlineKeyboardButton(text="👤 Owner Info Help", callback_data="admin:owner_help")],
        [InlineKeyboardButton(text="📣 Broadcast Help", callback_data="admin:broadcast_help"),
         InlineKeyboardButton(text="💾 Backup", callback_data="admin:backup")],
        [InlineKeyboardButton(text="🛠 Maintenance Mode", callback_data="admin:maintenance")],
        [InlineKeyboardButton(text="📜 Admin Logs", callback_data="admin:logs")],
        [InlineKeyboardButton(text="🏠 User Main Menu", callback_data="home")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- BACKUP HELPERS ---
def create_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"bot-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    with connect() as src:
        dst = sqlite3.connect(target)
        src.backup(dst)
        dst.close()
    return target

async def backup_loop(bot: Bot):
    if BACKUP_EVERY_HOURS <= 0:
        return
    while True:
        await asyncio.sleep(BACKUP_EVERY_HOURS * 3600)
        try:
            path = create_backup()
            old = sorted(BACKUP_DIR.glob("bot-backup-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)[10:]
            for p in old:
                p.unlink(missing_ok=True)
            await bot.send_document(OWNER_ID, FSInputFile(path), caption="🗄️ Scheduled Database Backup")
        except Exception:
            log.exception("Backup failed")

# --- CORE SCREEN HANDLER ---
async def show_screen(user_id: int, bot: Bot, target_message: Message, edit: bool = False):
    subbed, ch_url = await is_subscribed(bot, user_id)
    
    # AGAR CHANNEL JOIN NAHI HAI -> STRICT LOCK
    if not subbed:
        lock_text = (
            "⚠️ <b>Access Denied!</b>\n\n"
            "Bot ke sabhi features aur download links unlock karne ke liye pehle hamara <b>Official Channel</b> join karein.\n\n"
            "Channel join karne ke baad neeche diye gaye <b>Verify</b> button par click karein."
        )
        kb = subscribe_lock_kb(ch_url)
        if edit:
            try:
                await target_message.edit_text(lock_text, reply_markup=kb)
                return
            except Exception:
                pass
        await target_message.answer(lock_text, reply_markup=kb)
        return

    # MAINTENANCE CHECK
    if setting("maintenance", "0") == "1" and not is_owner(user_id):
        maint_text = "🛠 <b>Bot abhi maintenance mode me hai.</b>\nKripya thodi der baad dobara koshish karein."
        if edit:
            try:
                await target_message.edit_text(maint_text, reply_markup=None)
                return
            except Exception:
                pass
        await target_message.answer(maint_text)
        return

    # UNLOCKED -> SHOW MAIN MENU
    welcome_msg = setting("welcome", f"👋 <b>{BOT_TITLE}</b> me aapka swagat hai!\nNeeche diye gaye menu se chunav karein:").replace(r"\n", "\n")
    kb = build_main_menu(user_id)
    if edit:
        try:
            await target_message.edit_text(welcome_msg, reply_markup=kb)
            return
        except Exception:
            pass
    await target_message.answer(welcome_msg, reply_markup=kb)

dp = Dispatcher()

# --- USER COMMANDS ---
@dp.message(CommandStart())
async def start(message: Message, bot: Bot):
    save_user(message.from_user)
    await show_screen(message.from_user.id, bot, message, edit=False)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("Main menu ke liye /start karein. Agar aap owner hain, toh /admin use karein.")

@dp.message(Command("id"))
async def id_cmd(message: Message):
    await message.answer(f"Aapka Telegram User ID: <code>{message.from_user.id}</code>")

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_owner(message.from_user.id):
        return
    await message.answer("👑 <b>Super Admin Dashboard</b>", reply_markup=admin_dashboard_kb())

# --- ADMIN MANAGEMENT COMMANDS ---
@dp.message(Command("setchannel"))
async def set_channel_cmd(message: Message):
    if not is_owner(message.from_user.id):
        return
    # Format: /setchannel @channelusername | https://t.me/channelusername
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 1)]
    if len(parts) != 2:
        await message.answer("Format galat hai!\nSahi format:\n<code>/setchannel @YourChannel | https://t.me/YourChannel</code>")
        return
    set_setting("official_channel", parts[0])
    set_setting("official_channel_url", parts[1])
    await message.answer(f"✅ Official Channel set ho gaya:\nChannel ID: <code>{parts[0]}</code>\nLink: {parts[1]}")

@dp.message(Command("setowner"))
async def set_owner_cmd(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    if not raw:
        await message.answer("Format:\n<code>/setowner @AapkaUsername</code>")
        return
    set_setting("owner_username", raw)
    await message.answer(f"✅ Owner username set ho gaya: <code>{raw}</code>")

@dp.message(Command("addupdate"))
async def add_update(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 2)]
    if len(parts) < 2 or not parts[0] or not parts[1].startswith(("https://", "http://")):
        await message.answer("Format:\n<code>/addupdate Update_Title | https://download-link | Description</code>")
        return
    title, url = parts[0], parts[1]
    desc = parts[2] if len(parts) > 2 else ""
    with connect() as c:
        cur = c.execute("INSERT INTO updates(title,description,url,created_at) VALUES(?,?,?,?)", (title, desc, url, now()))
        uid = cur.lastrowid
    log_action("add_update", f"{uid}:{title}")
    await message.answer(f"✅ Naya update add ho gaya: <b>{title}</b> (ID: {uid})")

@dp.message(Command("delupdate"))
async def del_update(message: Message):
    if not is_owner(message.from_user.id):
        return
    try:
        uid = int(message.text.split(maxsplit=1)[1])
    except Exception:
        await message.answer("Format: <code>/delupdate UPDATE_ID</code>")
        return
    with connect() as c:
        c.execute("UPDATE updates SET active=0 WHERE id=?", (uid,))
    log_action("del_update", str(uid))
    await message.answer(f"🗑️ Update #{uid} delete kar diya gaya.")

@dp.message(Command("addbutton"))
async def add_button(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1].startswith(("https://", "http://")):
        await message.answer("Format:\n<code>/addbutton Button Title | https://link.com</code>")
        return
    with connect() as c:
        cur = c.execute("INSERT INTO buttons(title,url,position) VALUES(?,?,?)", (parts[0], parts[1], 100))
        bid = cur.lastrowid
    log_action("add_button", f"{bid}:{parts[0]}")
    await message.answer(f"✅ Button add ho gaya: <b>{parts[0]}</b> (ID: {bid})")

@dp.message(Command("delbutton"))
async def del_button(message: Message):
    if not is_owner(message.from_user.id):
        return
    try:
        bid = int(message.text.split(maxsplit=1)[1])
    except Exception:
        await message.answer("Format: <code>/delbutton BUTTON_ID</code>")
        return
    with connect() as c:
        c.execute("UPDATE buttons SET active=0 WHERE id=?", (bid,))
    log_action("del_button", str(bid))
    await message.answer(f"🗑️ Button #{bid} hata diya gaya.")

@dp.message(Command("set"))
async def set_config(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 1)]
    allowed = {"welcome", "youtube_url", "instagram_url", "support_url"}
    if len(parts) != 2 or parts[0] not in allowed:
        await message.answer("Allowed keys: welcome, youtube_url, instagram_url, support_url\nExample: <code>/set welcome | Swagat hai!</code>")
        return
    set_setting(parts[0], parts[1])
    await message.answer(f"✅ Setting updated: <code>{parts[0]}</code>")

@dp.message(Command("backup"))
async def backup_cmd(message: Message):
    if not is_owner(message.from_user.id):
        return
    path = create_backup()
    log_action("manual_backup", path.name)
    await message.answer_document(FSInputFile(path), caption=f"💾 Backup file: {path.name}")

@dp.message(F.chat.type == "private", F.reply_to_message, F.text.startswith("/broadcast"))
async def broadcast_reply(message: Message, bot: Bot):
    if not is_owner(message.from_user.id):
        return
    source = message.reply_to_message
    if source is None:
        await message.answer("Broadcast karne ke liye kisi post ko reply karke <code>/broadcast</code> likhein.")
        return
    await message.answer("📣 Broadcast shuru kiya ja raha hai...")
    with connect() as c:
        users = [r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE blocked=0").fetchall()]
        cur = c.execute("INSERT INTO broadcasts(at,source_chat,source_message_id,total,status) VALUES(?,?,?,?,?)",
                        (now(), str(source.chat.id), source.message_id, len(users), "running"))
        bid = cur.lastrowid
    sent = failed = 0
    for uid in users:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=source.chat.id, message_id=source.message_id)
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.copy_message(chat_id=uid, from_chat_id=source.chat.id, message_id=source.message_id)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            failed += 1
            with connect() as c:
                c.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
        except Exception:
            failed += 1
        if (sent + failed) % 100 == 0:
            with connect() as c:
                c.execute("UPDATE broadcasts SET sent=?,failed=? WHERE id=?", (sent, failed, bid))
    with connect() as c:
        c.execute("UPDATE broadcasts SET sent=?,failed=?,status='done' WHERE id=?", (sent, failed, bid))
    log_action("broadcast_done", f"id={bid},sent={sent},failed={failed}")
    await message.answer(f"✅ Broadcast Complete!\nTotal: {len(users)}\nSent: {sent}\nFailed: {failed}")

# --- CALLBACKS ---
@dp.callback_query(F.data == "verify_subscription")
async def verify_sub_handler(call: CallbackQuery, bot: Bot):
    subbed, _ = await is_subscribed(bot, call.from_user.id)
    if subbed:
        await call.answer("✅ Verification safal!", show_alert=False)
        await show_screen(call.from_user.id, bot, call.message, edit=True)
    else:
        await call.answer("❌ Aapne abhi tak channel join nahi kiya hai. Pehle join karein!", show_alert=True)

@dp.callback_query(F.data == "about_bot")
async def about_bot_handler(call: CallbackQuery, bot: Bot):
    subbed, _ = await is_subscribed(bot, call.from_user.id)
    if not subbed:
        await call.answer("Pehle channel join karein!", show_alert=True)
        return
    await call.answer()
    
    _, ch_url = get_channel_info()
    owner_u = setting("owner_username", OWNER_USERNAME) or "Not configured"
    
    about_text = (
        f"ℹ️ <b>All About {BOT_TITLE}</b>\n\n"
        f"👑 <b>Owner:</b> {owner_u}\n"
        f"📢 <b>Official Channel:</b> {ch_url or 'Not set'}\n\n"
        f"📌 <b>Description:</b>\n"
        f"Ye bot direct file downloads, exclusive links aur regular announcements ke liye banaya gaya hai.\n\n"
        f"Kisi bhi samasya ke liye support se sampark karein."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="home")]
    ])
    await call.message.edit_text(about_text, reply_markup=kb)

@dp.callback_query(F.data == "home")
async def home_callback(call: CallbackQuery, bot: Bot):
    await call.answer()
    await show_screen(call.from_user.id, bot, call.message, edit=True)

@dp.callback_query(F.data.startswith("updates:"))
async def show_updates(call: CallbackQuery, bot: Bot):
    subbed, _ = await is_subscribed(bot, call.from_user.id)
    if not subbed:
        await call.answer("Access Denied! Pehle channel join karein.", show_alert=True)
        return
    page = int(call.data.split(":")[1])
    with connect() as c:
        rows = c.execute("SELECT id,title FROM updates WHERE active=1 ORDER BY id DESC LIMIT 8 OFFSET ?", (page*8,)).fetchall()
        total = c.execute("SELECT COUNT(*) n FROM updates WHERE active=1").fetchone()["n"]
    buttons = [[InlineKeyboardButton(text=f"📦 {r['title']}"[:64], callback_data=f"update:{r['id']}")] for r in rows]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"updates:{page-1}"))
    if (page+1)*8 < total:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"updates:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="home")])
    await call.answer()
    await call.message.edit_text("📥 <b>Available Updates & Downloads:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("update:"))
async def update_detail(call: CallbackQuery, bot: Bot):
    subbed, _ = await is_subscribed(bot, call.from_user.id)
    if not subbed:
        await call.answer("Access Denied! Pehle channel join karein.", show_alert=True)
        return
    uid = int(call.data.split(":")[1])
    with connect() as c:
        row = c.execute("SELECT * FROM updates WHERE id=? AND active=1", (uid,)).fetchone()
    await call.answer()
    if not row:
        await call.message.answer("Ye update ab uplabdh nahi hai.")
        return
    created = row["created_at"][:10] if row["created_at"] else "N/A"
    text = (
        f"📦 <b>{row['title']}</b>\n\n"
        f"📝 <b>Description:</b> {row['description'] or 'Latest download available.'}\n"
        f"📅 <b>Date:</b> {created}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Download Now", url=row["url"])],
        [InlineKeyboardButton(text="⬅️ Back to Updates", callback_data="updates:0")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

# --- ADMIN CALLBACKS ---
@dp.callback_query(F.data == "admin:open")
async def admin_open_cb(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Access Denied!", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("👑 <b>Super Admin Dashboard</b>", reply_markup=admin_dashboard_kb())

@dp.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(call: CallbackQuery, bot: Bot):
    if not is_owner(call.from_user.id):
        await call.answer("Unauthorized!", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    await call.answer()
    if action == "stats":
        with connect() as c:
            users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            blocked = c.execute("SELECT COUNT(*) n FROM users WHERE blocked=1").fetchone()["n"]
            updates = c.execute("SELECT COUNT(*) n FROM updates WHERE active=1").fetchone()["n"]
            buttons = c.execute("SELECT COUNT(*) n FROM buttons WHERE active=1").fetchone()["n"]
            bcasts = c.execute("SELECT COUNT(*) n FROM broadcasts").fetchone()["n"]
        await call.message.answer(
            f"📊 <b>Live Bot Statistics</b>\n\n"
            f"👥 Total Users: {users}\n"
            f"🟢 Active Users: {users - blocked}\n"
            f"🚫 Blocked Users: {blocked}\n"
            f"📥 Active Updates: {updates}\n"
            f"🔗 Custom Buttons: {buttons}\n"
            f"📣 Total Broadcasts: {bcasts}"
        )
    elif action == "updates":
        with connect() as c:
            rows = c.execute("SELECT id,title,url FROM updates WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall()
        text = "📥 <b>Active Updates List:</b>\n\n" + ("\n".join(f"#{r['id']} {r['title']} — {r['url']}" for r in rows) or "No updates yet.")
        text += "\n\nNaya add karne ke liye: <code>/addupdate Title | URL | Desc</code>\nHatane ke liye: <code>/delupdate ID</code>"
        await call.message.answer(text)
    elif action == "add_update_help":
        await call.message.answer("Naya download link add karne ke liye chat me bhejein:\n<code>/addupdate App_Name | https://download-link.com | Ye latest version hai</code>")
    elif action == "add_button_help":
        await call.message.answer("Menu me naya custom button add karne ke liye chat me bhejein:\n<code>/addbutton Button Title | https://example.com</code>\nHatane ke liye: <code>/delbutton ID</code>")
    elif action == "channel_help":
        await call.message.answer("Official Channel link karne ke liye command bhejein:\n<code>/setchannel @YourChannelUsername | https://t.me/YourChannelUsername</code>\n\n⚠️ <i>Dhyan rahe: Bot ko apne channel me Admin zaroor banayein taaki wo membership check kar sake.</i>")
    elif action == "owner_help":
        await call.message.answer("About section me apna username set karne ke liye command bhejein:\n<code>/setowner @AapkaUsername</code>")
    elif action == "broadcast_help":
        await call.message.answer("Sabhi users ko message bhejne ke liye: Koi bhi message forward ya send karein aur uspar reply karke <code>/broadcast</code> likhein.")
    elif action == "backup":
        path = create_backup()
        log_action("manual_backup", path.name)
        await call.message.answer_document(FSInputFile(path), caption=f"💾 Backup: {path.name}")
    elif action == "maintenance":
        new = "0" if setting("maintenance", "0") == "1" else "1"
        set_setting("maintenance", new)
        await call.message.answer(f"🛠 Maintenance mode: <b>{'ON (Active)' if new == '1' else 'OFF (Inactive)'}</b>")
    elif action == "logs":
        with connect() as c:
            rows = c.execute("SELECT at,action,detail FROM logs ORDER BY id DESC LIMIT 10").fetchall()
        body = "\n".join(f"• {r['at']} — {r['action']}: {r['detail']}" for r in rows) or "No logs."
        await call.message.answer("<b>Recent Logs:</b>\n" + body[:3500])

async def main():
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Main menu"),
            BotCommand(command="admin", description="Owner Admin Panel"),
            BotCommand(command="help", description="Help & Usage"),
            BotCommand(command="id", description="Show Telegram ID"),
        ])
        asyncio.create_task(backup_loop(bot))
        log.info("Bot started successfully. Owner ID: %s", OWNER_ID)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
