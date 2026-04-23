# Udemy Free Course Alert Bot 🎓

Monitors [udemyfreebies.com](https://www.udemyfreebies.com/) and posts new free Udemy courses to a Discord channel via webhook.

## Features

- Scrapes free Udemy coupon links every 15 minutes
- Posts course title, thumbnail, coupon code, and expiry to Discord
- Deduplicates using PostgreSQL so you never get the same course twice
- Runs forever as a Render worker (free tier)

---

## Deployment (Render + Supabase)

### Step 1 — Get a free PostgreSQL database (Supabase)

1. Go to [supabase.com](https://supabase.com) → **New project** (free tier)
2. After it's ready: **Settings → Database → Connection string → URI**
3. Copy the `postgresql://...` connection string

### Step 2 — Get your Discord Webhook URL

1. Open your Discord server → channel settings → **Integrations → Webhooks**
2. Click **New Webhook** → copy the URL

### Step 3 — Deploy on Render

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → **New → Blueprint**
3. Connect your GitHub repo — Render will detect `render.yaml` automatically
4. Set these environment variables in the Render dashboard:
   - `DISCORD_WEBHOOK_URL` → your Discord webhook URL
   - `DATABASE_URL` → your Supabase connection string
5. Click **Deploy**

That's it! The bot will start immediately and post any new free courses to your Discord.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_WEBHOOK_URL` | ✅ | — | Discord webhook URL |
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `SOURCE_URL` | ❌ | `https://www.udemyfreebies.com/` | Source to scrape |
| `POLL_SECONDS` | ❌ | `900` | Seconds between checks (min 30) |
| `MAX_DETAILS_PER_RUN` | ❌ | `40` | Max courses to check per cycle |
| `RUN_ONCE` | ❌ | `false` | Exit after one cycle (for testing) |

## Local Testing

```bash
pip install -r requirements.txt

export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
export DATABASE_URL="postgresql://..."

# Single run (test mode)
python udemy_free_webhook.py --once

# Continuous mode
python udemy_free_webhook.py
```