# Udemy Freebies Discord Bot

Recommended deployment: GitHub Actions (free and easy).

## GitHub Actions deploy

1. Push this repository to GitHub.
2. In your GitHub repo, open `Settings` -> `Secrets and variables` -> `Actions`.
3. Create secret `DISCORD_WEBHOOK_URL` with your Discord webhook URL.
4. Open `Settings` -> `Actions` -> `General` -> `Workflow permissions`, and set `Read and write permissions` (required so workflow can commit `data/seen.sqlite3`).
5. Commit/push this project files (including `.github/workflows/udemy-bot.yml`).
6. Open `Actions` tab -> select `Udemy Bot` workflow -> click `Run workflow` once.
7. Confirm logs show successful run and Discord posts.

The workflow then runs automatically every 15 minutes using:

```yaml
cron: "*/15 * * * *"
timezone: "Asia/Phnom_Penh"
```

Notes:
- You can manually run from `Actions` and override `source_url` / `max_details_per_run` inputs.
- Workflow state is persisted in `data/seen.sqlite3` and auto-committed back to your repo.

## Local run (single cycle)

```bash
DISCORD_WEBHOOK_URL=... RUN_ONCE=1 python udemy_free_webhook.py
```

On Windows PowerShell:

```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
$env:RUN_ONCE="1"
python .\udemy_free_webhook.py
```

## Local run (continuous mode)

```bash
DISCORD_WEBHOOK_URL=... python udemy_free_webhook.py
```
