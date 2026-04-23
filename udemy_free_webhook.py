import hashlib
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------- CONFIG ----------------
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SOURCE_URL = os.environ.get("SOURCE_URL", "https://www.udemyfreebies.com/").strip()
DB_PATH = Path(os.environ.get("DB_PATH", "data/seen.sqlite3")).expanduser()

USER_AGENT = "UdemyFreeAlertBot/3.0"
TIMEOUT = 30


def env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer for {name}: {raw!r}") from exc

    if value < min_value:
        raise SystemExit(f"{name} must be >= {min_value}, got {value}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


POLL_SECONDS = env_int("POLL_SECONDS", 900, min_value=30)
MAX_DETAILS_PER_RUN = env_int("MAX_DETAILS_PER_RUN", 40, min_value=1)

if not WEBHOOK_URL:
    raise SystemExit("Missing DISCORD_WEBHOOK_URL environment variable")

parsed_source = urlparse(SOURCE_URL)
if not parsed_source.scheme or not parsed_source.netloc:
    raise SystemExit(f"Invalid SOURCE_URL: {SOURCE_URL!r}")


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} | {message}", flush=True)


# ---------------- DB ----------------
def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                id TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                first_seen_ts INTEGER
            )
            """
        )


def seen(item_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("SELECT 1 FROM seen WHERE id=?", (item_id,)).fetchone() is not None


def mark_seen(item_id: str, url: str, title: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR IGNORE INTO seen VALUES (?, ?, ?, strftime('%s','now'))",
            (item_id, url, title),
        )


# ---------------- HTTP ----------------
def build_session() -> requests.Session:
    http_session = requests.Session()
    http_session.headers.update({"User-Agent": USER_AGENT})

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD", "OPTIONS", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    http_session.mount("http://", adapter)
    http_session.mount("https://", adapter)
    return http_session


session = build_session()


def fetch(url: str) -> str:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


# ---------------- UTIL ----------------
def stable_id(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{url}".encode("utf-8")).hexdigest()


def extract_coupon(url: str) -> str:
    try:
        return parse_qs(urlparse(url).query).get("couponCode", [""])[0].strip()
    except Exception:
        return ""


# ---------------- PARSING ----------------
def parse_home(html: str, base: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    links: List[str] = []

    for anchor in soup.select("a[href]"):
        if "coupon detail" in anchor.get_text(" ", strip=True).lower():
            links.append(urljoin(base, anchor["href"]))

    return list(dict.fromkeys(links))


def get_udemy_url(html: str, base: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")

    for anchor in soup.select("a[href]"):
        href = urljoin(base, anchor["href"])
        if "udemy.com" not in href.lower():
            continue

        try:
            response = session.get(href, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            continue

        if "udemy.com/course/" in response.url:
            return response.url
    return None


# ---------------- DETAILS ----------------
def parse_expiry(text: str) -> Tuple[str, Optional[datetime]]:
    match = re.search(r"(\d+)\s+(days?|hours?|minutes?)", text.lower())
    if not match:
        return "", None

    amount = int(match.group(1))
    unit = match.group(2)
    now = datetime.now(timezone.utc)
    if "day" in unit:
        delta = timedelta(days=amount)
    elif "hour" in unit:
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(minutes=amount)

    return match.group(0), now + delta


def get_details(url: str) -> Tuple[str, str, str]:
    try:
        html = fetch(url)
        soup = BeautifulSoup(html, "lxml")
    except requests.RequestException as exc:
        log(f"Failed to fetch Udemy details for {url}: {exc}")
        return "Udemy Course", "", ""

    title = soup.title.get_text(strip=True) if soup.title else "Udemy Course"

    image = ""
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        image = og.get("content", "")

    text = soup.get_text(" ", strip=True)
    countdown, _expiry = parse_expiry(text)
    return title, image, countdown


# ---------------- DISCORD ----------------
def send_discord(title: str, url: str, coupon: str, image: str, countdown: str) -> None:
    description = "**Free Course**"
    if countdown:
        description += f"\nExpires in: {countdown}"

    embed = {
        "title": title,
        "url": url,
        "description": description,
        "footer": {"text": "Udemy Freebies Bot"},
    }

    if image:
        embed["image"] = {"url": image}

    if coupon:
        embed["fields"] = [{"name": "Coupon", "value": f"`{coupon}`", "inline": True}]

    response = session.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=TIMEOUT)
    response.raise_for_status()


# ---------------- MAIN ----------------
def run() -> None:
    base = f"{parsed_source.scheme}://{parsed_source.netloc}/"
    html = fetch(SOURCE_URL)
    detail_links = parse_home(html, base)

    posted = 0
    scanned = 0

    for link in detail_links[:MAX_DETAILS_PER_RUN]:
        scanned += 1
        try:
            detail_html = fetch(link)
            udemy_url = get_udemy_url(detail_html, base)
            if not udemy_url:
                continue

            coupon = extract_coupon(udemy_url)
            title, image, countdown = get_details(udemy_url)
            item_id = stable_id(title, udemy_url + coupon)

            if seen(item_id):
                continue

            send_discord(title, udemy_url, coupon, image, countdown)
            mark_seen(item_id, udemy_url, title)
            posted += 1
        except Exception as exc:
            log(f"Error while processing {link}: {exc}")

    log(f"Cycle complete: scanned={scanned}, posted={posted}")


def main() -> None:
    log("Bot started")
    init_db()

    while True:
        cycle_started = time.time()
        try:
            run()
        except Exception as exc:
            log(f"Loop error: {exc}")

        elapsed = int(time.time() - cycle_started)
        sleep_for = max(0, POLL_SECONDS - elapsed)
        if sleep_for:
            time.sleep(sleep_for)


def run_once() -> int:
    log("Bot started (single-run mode)")
    init_db()
    try:
        run()
        return 0
    except Exception as exc:
        log(f"Fatal error in single-run mode: {exc}")
        return 1


if __name__ == "__main__":
    single_run = env_bool("RUN_ONCE", default=False) or "--once" in sys.argv
    if single_run:
        raise SystemExit(run_once())
    main()
