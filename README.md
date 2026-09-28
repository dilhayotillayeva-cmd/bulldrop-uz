# BULLDROP BOT

## Render Environment Variables
- `BOT_TOKEN` — Telegram bot token
- `ADMIN_IDS` — admin Telegram ID(s), comma-separated
- `DB_PATH` — optional, default `bulldrop.sqlite3`

The bot runs Telegram polling and also exposes `/health` for Render/UptimeRobot.

## Features
- Uzbek / Russian language selection
- Mandatory channel subscription
- 5x5 free-key box system
- Referral coins
- Profile and TOP
- Tasks: channel subscription and external link/video/like tasks
- One-time task rewards
- Admin task add/remove/statistics
- Existing statistics, users, rewards, promo codes and channels use the same SQLite table names so existing data is not intentionally deleted.
