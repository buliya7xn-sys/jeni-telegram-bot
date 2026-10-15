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

# Token resolution (FadeHost default: TELEGRAM_BOT_TOKEN ya standard BOT_TOKEN)
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
REQUIRED_CHANNELS = [x.strip() for x in os.getenv("REQUIRED_CHANNELS", "").split(",") if x.strip()]
DB_PATH = Path(os.getenv("DB_PATH", "data/bot.sqlite3"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))
BACKUP_EVERY_HOURS = max(0, int(os.getenv("BACKUP_EVERY_HOURS", "24") or 24))
BOT_TITLE = os.getenv("BOT_TITLE", "Personal Update Bot")
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "").strip()
INSTAGRAM_URL = os.getenv("INSTAGRAM_URL", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()

if not BOT_TOKEN:
    raise SystemExit("Error: BOT_TOKEN ya TELEGRAM_BOT_TOKEN missing hai!")
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
            "welcome": f"👋 <b>{BOT_TITLE}</b> में आपका स्वागत है!\n\nनीचे दिए गए मेन्यू से विकल्प चुनें:",
            "maintenance": "0",
            "official_channel_url": OFFICIAL_CHANNEL_URL,
            "youtube_url": YOUTUBE_URL,
            "instagram_url": INSTAGRAM_URL,
            "support_url": SUPPORT_URL,
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
    return user_id == OWNER_ID

def save_user(user):
    with connect() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name,started_at,last_seen,blocked)
        VALUES(?,?,?,?,?,0) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen""",
        (user.id, user.username or "", user.first_name or "", now(), now()))

# --- MEMBERSHIP CHECK (FORCE SUBSCRIBE) ---
def get_required_channels():
    channels = list(REQUIRED_CHANNELS)
    if OFFICIAL_CHANNEL and OFFICIAL_CHANNEL not in channels:
        channels.append(OFFICIAL_CHANNEL)
    return list(dict.fromkeys(channels))

async def check_membership(bot: Bot, user_id: int):
    channels = get_required_channels()
    if not channels:
        return True, []
    missing = []
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                missing.append(channel)
        except Exception as e:
            log.warning("Membership check failed for %s: %s", channel, e)
            missing.append(channel)
    return not missing, missing

def channel_url(channel):
    if channel.startswith("@"):
        return "https://t.me/" + channel[1:]
    return setting("official_channel_url", OFFICIAL_CHANNEL_URL) or "https://t.me/"

def subscribe_kb(missing):
    rows = []
    for i, channel in enumerate(missing, 1):
        rows.append([InlineKeyboardButton(text=f"📢 Join Channel {i}", url=channel_url(channel))])
    rows.append([InlineKeyboardButton(text="✅ I've Joined", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- KEYBOARDS ---
def main_kb():
    rows = [
        [InlineKeyboardButton(text="📥 New Update Download", callback_data="updates:0")],
    ]
    links = [
        ("▶️ YouTube", setting("youtube_url", YOUTUBE_URL)),
        ("📸 Instagram", setting("instagram_url", INSTAGRAM_URL)),
        ("📢 Telegram Channel", setting("official_channel_url", OFFICIAL_CHANNEL_URL)),
        ("🆘 Help & Support", setting("support_url", SUPPORT_URL)),
    ]
    for label, url in links:
        if url:
            rows.append([InlineKeyboardButton(text=label, url=url)])
    with connect() as c:
        buttons = c.execute("SELECT id,title,url FROM buttons WHERE active=1 ORDER BY position,id").fetchall()
    for b in buttons:
        rows.append([InlineKeyboardButton(text=b["title"][:64], url=b["url"])])
    rows.append([
        InlineKeyboardButton(text="ℹ️ About Bot", callback_data="about"),
        InlineKeyboardButton(text="🔄 Check Subscription", callback_data="check_sub")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_kb():
    rows = [
        [InlineKeyboardButton(text="📊 Bot Statistics", callback_data="admin:stats"),
         InlineKeyboardButton(text="📥 Manage Updates", callback_data="admin:updates")],
        [InlineKeyboardButton(text="➕ Add Update", callback_data="admin:add_update_help"),
         InlineKeyboardButton(text="➕ Add URL Button", callback_data="admin:add_button_help")],
        [InlineKeyboardButton(text="✏️ Settings Help", callback_data="admin:settings_help"),
         InlineKeyboardButton(text="📣 Broadcast Help", callback_data="admin:broadcast_help")],
        [InlineKeyboardButton(text="💾 Create Backup", callback_data="admin:backup"),
         InlineKeyboardButton(text="🛠 Toggle Maintenance", callback_data="admin:maintenance")],
        [InlineKeyboardButton(text="📜 Recent Admin Logs", callback_data="admin:logs")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- BACKUP SYSTEM ---
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
            log.exception("Scheduled backup failed")

async def send_home(message: Message, bot: Bot):
    ok, missing = await check_membership(bot, message.from_user.id)
    if not ok:
        await message.answer(
            "⚠️ <b>Access Denied!</b>\n\nबॉट के सभी फीचर्स इस्तेमाल करने के लिए हमारे चैनल को जॉइन करें।",
            reply_markup=subscribe_kb(missing)
        )
        return
    if setting("maintenance", "0") == "1" and not is_owner(message.from_user.id):
        await message.answer("🛠 बॉट अभी मेंटेनेंस में है। कृपया कुछ समय बाद प्रयास करें।")
        return
    await message.answer(setting("welcome", f"👋 <b>{BOT_TITLE}</b> में आपका स्वागत है!"), reply_markup=main_kb())

dp = Dispatcher()

# --- COMMAND HANDLERS ---
@dp.message(CommandStart())
async def start(message: Message, bot: Bot):
    save_user(message.from_user)
    await send_home(message, bot)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("मेनू खोलने के लिए /start का उपयोग करें। यदि आप ओनर हैं, तो /admin का उपयोग करें।")

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_owner(message.from_user.id):
        return
    await message.answer("⚙️ <b>Super Admin Dashboard</b>", reply_markup=admin_kb())

@dp.message(Command("id"))
async def id_cmd(message: Message):
    await message.answer(f"आपका Telegram User ID: <code>{message.from_user.id}</code>")

@dp.message(Command("addupdate"))
async def add_update(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 2)]
    if len(parts) < 2 or not parts[0] or not parts[1].startswith(("https://", "http://")):
        await message.answer("उपयोग प्रारूप:\n<code>/addupdate Title | https://download-link | optional description</code>")
        return
    title, url = parts[0], parts[1]
    description = parts[2] if len(parts) > 2 else ""
    with connect() as c:
        cur = c.execute("INSERT INTO updates(title,description,url,created_at) VALUES(?,?,?,?)", (title, description, url, now()))
        uid = cur.lastrowid
    log_action("add_update", f"{uid}:{title}")
    await message.answer(f"✅ अपडेट सफलतापूर्वक जोड़ा गया: <b>{title}</b> (ID {uid})")

@dp.message(Command("delupdate"))
async def del_update(message: Message):
    if not is_owner(message.from_user.id):
        return
    try:
        uid = int(message.text.split(maxsplit=1)[1])
    except Exception:
        await message.answer("उपयोग प्रारूप: <code>/delupdate UPDATE_ID</code>")
        return
    with connect() as c:
        c.execute("UPDATE updates SET active=0 WHERE id=?", (uid,))
    log_action("deactivate_update", str(uid))
    await message.answer(f"🗑️ अपडेट {uid} हटा दिया गया है।")

@dp.message(Command("addbutton"))
async def add_button(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1].startswith(("https://", "http://")):
        await message.answer("उपयोग प्रारूप:\n<code>/addbutton Button title | https://url</code>")
        return
    with connect() as c:
        cur = c.execute("INSERT INTO buttons(title,url,position) VALUES(?,?,?)", (parts[0], parts[1], 100))
        bid = cur.lastrowid
    log_action("add_button", f"{bid}:{parts[0]}")
    await message.answer(f"✅ बटन जोड़ा गया (ID {bid}): {parts[0]}")

@dp.message(Command("delbutton"))
async def del_button(message: Message):
    if not is_owner(message.from_user.id):
        return
    try:
        bid = int(message.text.split(maxsplit=1)[1])
    except Exception:
        await message.answer("उपयोग प्रारूप: <code>/delbutton BUTTON_ID</code>")
        return
    with connect() as c:
        c.execute("UPDATE buttons SET active=0 WHERE id=?", (bid,))
    log_action("deactivate_button", str(bid))
    await message.answer(f"🗑️ बटन {bid} हटा दिया गया है।")

@dp.message(Command("set"))
async def set_config(message: Message):
    if not is_owner(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [x.strip() for x in raw.split("|", 1)]
    allowed = {"welcome", "youtube_url", "instagram_url", "official_channel_url", "support_url"}
    if len(parts) != 2 or parts[0] not in allowed:
        await message.answer("स्वीकृत सेटिंग्स: welcome, youtube_url, instagram_url, official_channel_url, support_url\nउदाहरण:\n<code>/set youtube_url | https://youtube.com/@channel</code>")
        return
    set_setting(parts[0], parts[1])
    await message.answer(f"✅ सेटिंग अपडेट हो गई: <code>{parts[0]}</code>")

@dp.message(Command("backup"))
async def backup_cmd(message: Message):
    if not is_owner(message.from_user.id):
        return
    path = create_backup()
    log_action("manual_backup", path.name)
    await message.answer_document(FSInputFile(path), caption=f"💾 बैकअप तैयार: {path.name}")

@dp.message(F.chat.type == "private", F.reply_to_message, F.text.startswith("/broadcast"))
async def broadcast_reply(message: Message, bot: Bot):
    if not is_owner(message.from_user.id):
        return
    source = message.reply_to_message
    if source is None:
        await message.answer("प्रसारण करने के लिए किसी संदेश/पोस्ट को रिप्लाई करते हुए <code>/broadcast</code> लिखें।")
        return
    await message.answer("📣 ब्रॉडकास्ट शुरू किया जा रहा है...")
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
    await message.answer(f"✅ ब्रॉडकास्ट समाप्त!\nकुल: {len(users)}\nसफल: {sent}\nअसफल: {failed}")

# --- CALLBACK QUERY HANDLERS ---
@dp.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    ok, missing = await check_membership(bot, call.from_user.id)
    await call.answer()
    if ok:
        await call.message.edit_text("✅ सदस्यता सत्यापित हो गई है! यहाँ आपका मुख्य मेन्यू है:", reply_markup=None)
        await call.message.answer(setting("welcome", f"👋 <b>{BOT_TITLE}</b> में आपका स्वागत है!"), reply_markup=main_kb())
    else:
        await call.message.edit_text(
            "❌ आप अभी भी आवश्यक चैनल से नहीं जुड़े हैं। कृपया जॉइन करें:",
            reply_markup=subscribe_kb(missing)
        )

@dp.callback_query(F.data.startswith("updates:"))
async def show_updates(call: CallbackQuery, bot: Bot):
    ok, missing = await check_membership(bot, call.from_user.id)
    if not ok:
        await call.answer("कृपया पहले चैनल जॉइन करें।", show_alert=True)
        await call.message.answer("⚠️ कृपया पहले चैनल जॉइन करें:", reply_markup=subscribe_kb(missing))
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
    await call.message.answer("📥 <b>New Update Downloads</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("update:"))
async def update_detail(call: CallbackQuery, bot: Bot):
    ok, missing = await check_membership(bot, call.from_user.id)
    if not ok:
        await call.answer("कृपया पहले चैनल जॉइन करें।", show_alert=True)
        return
    uid = int(call.data.split(":")[1])
    with connect() as c:
        row = c.execute("SELECT * FROM updates WHERE id=? AND active=1", (uid,)).fetchone()
    await call.answer()
    if not row:
        await call.message.answer("यह अपडेट अब उपलब्ध नहीं है।")
        return
    created = row["created_at"][:10] if row["created_at"] else "N/A"
    text = (
        f"📦 <b>{row['title']}</b>\n\n"
        f"📝 <b>Description:</b> {row['description'] or 'नवीनतम अपडेट'}\n"
        f"📅 <b>Updated:</b> {created}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Download Now", url=row["url"])],
        [InlineKeyboardButton(text="⬅️ Back to Updates", callback_data="updates:0")],
    ])
    await call.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "home")
async def home_callback(call: CallbackQuery, bot: Bot):
    ok, missing = await check_membership(bot, call.from_user.id)
    await call.answer()
    if not ok:
        await call.message.answer("⚠️ कृपया पहले चैनल जॉइन करें:", reply_markup=subscribe_kb(missing))
    else:
        await call.message.answer(setting("welcome", f"👋 <b>{BOT_TITLE}</b> में आपका स्वागत है!"), reply_markup=main_kb())

@dp.callback_query(F.data == "about")
async def about_callback(call: CallbackQuery):
    await call.answer()
    await call.message.answer(f"ℹ️ <b>About {BOT_TITLE}</b>\n\nयह आपका व्यक्तिगत अपडेट और संसाधन वितरण बॉट है। मेन्यू खोलने के लिए /start का उपयोग करें।")

@dp.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(call: CallbackQuery, bot: Bot):
    if not is_owner(call.from_user.id):
        await call.answer("अनधिकृत अनुरोध!", show_alert=True)
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
            f"📊 <b>Bot Statistics</b>\n\n"
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
        text = "📥 <b>सक्रिय अपडेट्स:</b>\n\n" + ("\n".join(f"#{r['id']} {r['title']} — {r['url']}" for r in rows) or "कोई अपडेट उपलब्ध नहीं है।")
        text += "\n\nजोड़ने के लिए: <code>/addupdate Title | URL | description</code>\nहटाने के लिए: <code>/delupdate ID</code>"
        await call.message.answer(text)
    elif action == "add_update_help":
        await call.message.answer("नया अपडेट जोड़ने के लिए कमांड:\n<code>/addupdate Title | https://download-link | विवरण</code>")
    elif action == "add_button_help":
        await call.message.answer("नया URL बटन जोड़ने के लिए कमांड:\n<code>/addbutton Title | https://example.com</code>\nहटाने के लिए: <code>/delbutton ID</code>")
    elif action == "settings_help":
        await call.message.answer(
            "सेटिंग बदलने के लिए उदाहरण:\n"
            "<code>/set welcome | आपका स्वागत संदेश</code>\n"
            "<code>/set youtube_url | https://youtube.com/@channel</code>\n"
            "<code>/set instagram_url | https://instagram.com/user</code>\n"
            "<code>/set official_channel_url | https://t.me/channel</code>\n"
            "<code>/set support_url | https://t.me/username</code>"
        )
    elif action == "broadcast_help":
        await call.message.answer("ब्रॉडकास्ट प्रक्रिया: किसी भी संदेश को यहाँ भेजें और उस पर रिप्लाई करके <code>/broadcast</code> लिखें।")
    elif action == "backup":
        path = create_backup()
        log_action("manual_backup", path.name)
        await call.message.answer_document(FSInputFile(path), caption=f"💾 बैकअप तैयार: {path.name}")
    elif action == "maintenance":
        new = "0" if setting("maintenance", "0") == "1" else "1"
        set_setting("maintenance", new)
        await call.message.answer(f"🛠 मेंटेनेंस मोड अब <b>{'सक्रिय (ON)' if new == '1' else 'निष्क्रिय (OFF)'}</b> है।")
    elif action == "logs":
        with connect() as c:
            rows = c.execute("SELECT at,action,detail FROM logs ORDER BY id DESC LIMIT 10").fetchall()
        body = "\n".join(f"• {r['at']} — {r['action']}: {r['detail']}" for r in rows) or "कोई लॉग नहीं मिला।"
        await call.message.answer("<b>हाल के एडमिन लॉग:</b>\n" + body[:3500])

async def main():
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="मुख्य मेनू खोलें"),
            BotCommand(command="help", description="सहायता"),
            BotCommand(command="id", description="अपना Telegram ID देखें"),
        ])
        asyncio.create_task(backup_loop(bot))
        log.info("Bot started successfully. Owner ID: %s", OWNER_ID)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
