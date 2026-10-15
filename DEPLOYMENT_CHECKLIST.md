# Deployment / Testing Checklist

- [ ] Create bot with @BotFather and store token privately.
- [ ] Set OWNER_ID to your numeric Telegram ID.
- [ ] Configure official channel and required subscription channel(s).
- [ ] Add bot as admin in required channel(s); verify membership check.
- [ ] Add bot as admin to official channel.
- [ ] Start locally/test host and run `/start`, `/id`, `/admin`.
- [ ] Add one test update using `/addupdate`.
- [ ] Add one test URL button using `/addbutton`.
- [ ] Test channel join gate with a second Telegram account.
- [ ] Test manual broadcast with a small test audience only.
- [ ] Create `/backup` and download the attached backup to a separate device/storage.
- [ ] Test restore procedure on a separate test copy.
- [ ] Confirm host has persistent storage for `data/` and `backups/`.
- [ ] Set up host restart-on-failure and logs.
- [ ] Keep repository private and never commit `.env`.
