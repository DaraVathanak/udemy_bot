# Udemy Freebies Discord Bot

This project is now prepared for an always-on Linux VM deployment (recommended: Oracle Cloud Always Free VM).

## 1) VM prerequisites (Ubuntu)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

## 2) Clone and install

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone <your-repo-url> udemy-webhook
sudo chown -R ubuntu:ubuntu /opt/udemy-webhook
cd /opt/udemy-webhook
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3) Configure environment variables

```bash
sudo mkdir -p /var/lib/udemy-webhook
sudo chown -R ubuntu:ubuntu /var/lib/udemy-webhook
sudo cp deploy/oci/udemy-webhook.env.example /etc/udemy-webhook.env
sudo nano /etc/udemy-webhook.env
```

At minimum, set `DISCORD_WEBHOOK_URL`.

## 4) Install and start systemd service

```bash
sudo cp deploy/oci/udemy-webhook.service /etc/systemd/system/udemy-webhook.service
sudo systemctl daemon-reload
sudo systemctl enable --now udemy-webhook
```

## 5) Verify

```bash
sudo systemctl status udemy-webhook
sudo journalctl -u udemy-webhook -f
```

## Notes

- The SQLite state is persisted at `/var/lib/udemy-webhook/seen.sqlite3`.
- The service auto-restarts on failure.
- Poll interval is controlled by `POLL_SECONDS` in `/etc/udemy-webhook.env`.
