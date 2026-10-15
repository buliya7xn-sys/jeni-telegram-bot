# Personal Telegram Update Bot (Starter Project)

A Telegram bot starter with:
- Required channel subscription check (one or multiple channels)
- User database for people who have started the bot
- Main menu with update list, YouTube, Instagram, official channel, support links
- Dynamic download updates (add/hide from Telegram with commands)
- Dynamic URL buttons
- Owner-only Telegram admin panel
- Manual channel-post/file-to-DM broadcast to users who previously started the bot
- Statistics, admin logs, maintenance mode
- SQLite database backups, scheduled backups, and a backup file sent to owner chat
- Dockerfile for deployment

## Important Telegram limitations
1. Telegram bots cannot automatically add users to a channel. Users must join themselves.
2. A bot cannot DM arbitrary channel subscribers. It can message only users who have initiated the bot, and only while Telegram allows it.
3. Channel members are not automatically bot users. This bot's broadcast target is its saved user database.
4. The bot must be an administrator in each required channel to check membership, and in the official channel to receive channel posts.
5. Manual broadcast is intentionally used by default; there is no automatic DM blast on every channel post.
6. Test in a private/small test group first. Telegram rate limits and blocked users affect delivery.

## Setup
1. Create a bot with Telegram's official `@BotFather` and copy its token.
2. Copy `.env.example` to `.env`.
3. Set `BOT_TOKEN`, `OWNER_ID`, `OFFICIAL_CHANNEL`, `OFFICIAL_CHANNEL_URL`, and `REQUIRED_CHANNELS`.
4. Add the bot as an administrator in the required channel(s), with permission to view members; add it as an administrator in the official channel to receive channel posts.
5. Install Python 3.11+ (3.12 recommended), then run:

   ```bash
   python -m venv .venv
   # Windows: .venv\\Scripts\\activate
   # macOS/Linux:
   source .venv/bin/activate
   pip install -r requirements.txt
   python main.py
   ```

6. Open your bot in Telegram and send `/start`. Use `/id` to find your numeric ID; set it in `.env` and restart.

## Owner commands
- `/admin` — open Telegram-only owner panel
- `/id` — show your numeric Telegram ID
- `/users` — user and content counts
- `/addupdate Title | https://download-url | optional description`
- `/delupdate UPDATE_ID` — hide update
- `/addbutton Button title | https://example.com`
- `/delbutton BUTTON_ID` — hide URL button
- `/set welcome | Welcome message`
- `/set youtube_url | https://youtube.com/@yourchannel`
- `/set instagram_url | https://instagram.com/yourprofile`
- `/set official_channel_url | https://t.me/yourchannel`
- `/set support_url | https://t.me/support`
- `/backup` — create and send database backup
- `/restorehelp` — safe manual restore instructions
- `/logs` — recent admin logs

## Manual broadcast (channel post, text, photo, video, document)
1. In the bot's private chat, send or forward/copy the post/media you want to broadcast.
2. Reply to that message with `/broadcast`.
3. Confirm the target and delivery report in chat. The target list is users who have started the bot.

Note: the broadcast handler uses Telegram's copy-message operation. Protected content may not be copyable. For channel posts, you can copy/forward the desired post into the bot's private chat first.

## Backups: where they live
- Server local path: `backups/bot-backup-YYYYMMDD-HHMMSS.sqlite3`
- Main live database: `data/bot.sqlite3`
- Manual and scheduled backups are also sent as a document to the owner Telegram chat.
- The latest 10 scheduled/local backup files are retained by the automatic routine.
- Keep a copy outside the hosting server (for example, download the file to your own phone/computer or private storage). A backup stored only on the same server will not protect against server loss.
- On hosting, ensure `data/` and `backups/` are on persistent storage. Ephemeral container filesystems may be erased on redeploy/restart.
- `.env` contains secrets and is deliberately excluded from Git.

## GitHub / deployment
Push source files to a private GitHub repository. Do not commit `.env` or a bot token.
The Dockerfile is included, but hosting requirements vary. A host must support a persistent Python process or Docker container and persistent storage. GitHub itself does not run a long-lived bot. Configure a deployment process on your host; do not assume a push automatically deploys unless the host integration is set up.

## Before production
This is a starter codebase, not a claim of production certification. Test with your own bot/channel, verify backup restore, set a private repository, protect the token, and review hosting-specific persistence/restart behavior before inviting a large audience.
