# Jeni26 Telegram Bot — Updated

This package preserves the uploaded `main.py` bot's existing start flow, force-subscribe Join/Verify buttons, update/download menu, custom URL buttons, owner panel, SQLite user tracking, broadcast, logs, maintenance mode, and scheduled/manual database backups, while adding the features listed below.

## Added features
- Database backup restore: owner sends a `.sqlite3`, `.sqlite`, or `.db` backup in the bot's private chat with caption `/restore`. Use `/restorehelp` for instructions. Restore overwrites the current database; take a backup first.
- Feedback album: `/setfeedbackchannel @channel`, `/feedback_album`, send up to 10 photos, `/feedback_done`, then send the caption. Bot posts the album and tracks its message IDs.
- Tracked-post cleanup: `/cleanup_posts @channel` deletes only bot-created feedback albums tracked in this database, not all channel history.
- Group welcome message: `/setwelcome Welcome {name}! Please read the rules.`
- Group moderation: anti-link, custom profanity word list, basic adult-content keyword filter, warning tracking, 1-day ban on second violation, 7-day ban on third, permanent ban on a later violation.
- Moderation controls: `/modsettings`, `/antilink_on`, `/antilink_off`, `/profanity_on`, `/profanity_off`, `/adultfilter_on`, `/adultfilter_off`, `/setprofanity word1,word2`, `/setallowedlinks t.me/,example.com`.
- Warning tools: `/warnings` and reply to a user's message with `/resetwarnings`.
- Support tickets: `/ticket your issue`; owner can reply with `/ticketreply TICKET_ID your reply`.
- Admin panel has added help buttons for moderation, feedback albums, and tracked-post cleanup.

## Important limitations and permissions
- The same Telegram bot token means the bot identity stays the same when moving hosting. If the old SQLite backup is restored, saved users and their data return; they do not need to send `/start` again just because hosting changed. Broadcasts can reach saved users who previously started the bot and have not blocked it. Users who are only channel subscribers but never started the bot are not in the bot user database and cannot be DM'd by the bot.
- If you use a different bot token/new bot account, users must start that new bot themselves; the old bot's chat cannot be transferred.
- Add the bot as admin in the target group with Delete Messages and Ban Users permissions for moderation. Add it as admin in the feedback channel to post albums and delete its tracked posts.
- Profanity and adult-content checks are keyword-based. They do not reliably detect explicit images/videos; a real visual moderation service would be needed for that.
- Cleanup only deletes tracked bot-created posts. Telegram permissions and API restrictions apply; this tool does not erase all channel history.
- Test in a private test group/channel before production. Features are code-added but have not been live-tested against your FadeHost deployment or your actual Telegram channels.

## Deploy
1. Upload these files to your private GitHub repo or hosting project.
2. Configure the environment variables in `.env` / hosting environment. Never commit your real token.
3. Install dependencies from `requirements.txt` and run `python main.py`.
4. Configure `OWNER_ID`, `OFFICIAL_CHANNEL`, `OFFICIAL_CHANNEL_URL`, and optionally `FEEDBACK_CHANNEL`.
5. Keep `data/` on persistent storage. A server-local backup alone will be lost if the host deletes its disk; download the backup to your device or secure storage.

## Backup migration steps
1. On old server, send `/backup` to the bot and download the `.sqlite3` file it returns.
2. Deploy the updated bot using the same token and owner ID.
3. Send the backup file to the bot in private chat with caption `/restore`.
4. Confirm restore success. Then test `/admin`, `/start`, and a small broadcast test.
