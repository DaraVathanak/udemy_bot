import os
import re
import time
import sqlite3
import hashlib
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SOURCE_URL = os.environ.get("SOURCE_URL", "https://www.udemyfreebies.com/").strip()
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "900"))
DB_PATH = os.environ.get("DB_PATH", "seen.sqlite3")
MAX_DETAILS_PER_RUN = int(os.environ.get("MAX_DETAILS_PER_RUN", "40"))

USER_AGENT = "UdemyFreeAlertBot/2.0"
TIMEOUT = 30

if not WEBHOOK_URL:
    raise SystemExit("❌ Missing DISCORD_WEBHOOK_URL environment variable")

# ---------------- DB ----------------
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                id TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                first_seen_ts INTEGER
            )
        """)

def seen(item_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("SELECT 1 FROM seen WHERE id=?", (item_id,)).fetchone() is not None

def mark_seen(item_id: str, url: str, title: str):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR IGNORE INTO seen VALUES (?, ?, ?, strftime('%s','now'))",
            (item_id, url, title)
        )

# ---------------- HTTP ----------------
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

def fetch(url: str) -> str:
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

# ---------------- UTIL ----------------
def stable_id(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{url}".encode()).hexdigest()

def extract_coupon(url: str) -> str:
    try:
        return parse_qs(urlparse(url).query).get("couponCode", [""])[0]
    except:
        return ""

# ---------------- PARSING ----------------
def parse_home(html: str, base: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    links = []

    for a in soup.select("a[href]"):
        if "coupon detail" in a.get_text(" ", strip=True).lower():
            links.append(urljoin(base, a["href"]))

    return list(dict.fromkeys(links))

def get_udemy_url(html: str, base: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")

    for a in soup.select("a[href]"):
        href = urljoin(base, a["href"])
        if "udemy.com" in href:
            try:
                r = session.get(href, timeout=TIMEOUT)
                if "udemy.com/course/" in r.url:
                    return r.url
            except:
                continue
    return None

# ---------------- DETAILS ----------------
def parse_expiry(text: str):
    match = re.search(r"(\d+)\s+(day|hour|minute)", text.lower())
    if not match:
        return "", None

    n = int(match.group(1))
    unit = match.group(2)

    now = datetime.now(timezone.utc)
    delta = timedelta(days=n) if "day" in unit else timedelta(hours=n) if "hour" in unit else timedelta(minutes=n)
    return match.group(0), now + delta

def get_details(url: str):
    try:
        html = fetch(url)
        soup = BeautifulSoup(html, "lxml")

        title = soup.title.string.strip() if soup.title else "Udemy Course"

        image = ""
        og = soup.find("meta", property="og:image")
        if og:
            image = og.get("content", "")

        text = soup.get_text(" ", strip=True)
        countdown, expiry = parse_expiry(text)

        return title, image, countdown, expiry
    except:
        return "Udemy Course", "", "", None

# ---------------- DISCORD ----------------
def send_discord(title, url, coupon, image, countdown, expiry):
    desc = f"**Free Course**"
    if countdown:
        desc += f"\n⏳ {countdown}"

    embed = {
        "title": title,
        "url": url,
        "description": desc,
        "footer": {"text": "Udemy Freebies Bot"}
    }

    if image:
        embed["image"] = {"url": image}

    if coupon:
        embed["fields"] = [{"name": "Coupon", "value": f"`{coupon}`", "inline": True}]

    requests.post(WEBHOOK_URL, json={"embeds": [embed]})

# ---------------- MAIN ----------------
def run():
    base = f"{urlparse(SOURCE_URL).scheme}://{urlparse(SOURCE_URL).netloc}/"
    html = fetch(SOURCE_URL)
    detail_links = parse_home(html, base)

    posted = 0

    for link in detail_links[:MAX_DETAILS_PER_RUN]:
        try:
            detail_html = fetch(link)
            udemy_url = get_udemy_url(detail_html, base)

            if not udemy_url:
                continue

            coupon = extract_coupon(udemy_url)
            title, image, countdown, expiry = get_details(udemy_url)

            item_id = stable_id(title, udemy_url + coupon)

            if seen(item_id):
                continue

            send_discord(title, udemy_url, coupon, image, countdown, expiry)
            mark_seen(item_id, udemy_url, title)

            posted += 1

        except Exception as e:
            print("Error:", e)

    print(f"Posted {posted} new courses")

def main():
    print("🚀 Bot started")
    init_db()

    while True:
        try:
            run()
        except Exception as e:
            print("Loop error:", e)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()