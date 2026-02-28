import os
import re
import time
import json
import sqlite3
import hashlib
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1477280261820387338/-NZmXFVyUabiiTYDDSJX33-_-bna55QeCxBZmxjxUEitYjZ_pvxpYE8ePkYot4fzKJ2e").strip()
SOURCE_URL = os.environ.get("SOURCE_URL", "https://www.udemyfreebies.com/").strip()
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "900"))
DB_PATH = os.environ.get("DB_PATH", "seen.sqlite3")

USER_AGENT = "UdemyFreeAlertBot/1.0"
TIMEOUT = 30

# How many coupon detail pages to process per poll (avoid hammering sites)
MAX_DETAILS_PER_RUN = int(os.environ.get("MAX_DETAILS_PER_RUN", "40"))

if not WEBHOOK_URL:
    raise SystemExit("Missing DISCORD_WEBHOOK_URL. Set it as an environment variable.")
if not SOURCE_URL:
    raise SystemExit("Missing SOURCE_URL.")

# ---------------- DB ----------------
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                first_seen_ts INTEGER NOT NULL
            )
        """)
        con.commit()

def seen(item_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT 1 FROM seen WHERE id = ?", (item_id,)).fetchone()
        return row is not None

def mark_seen(item_id: str, url: str, title: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR IGNORE INTO seen (id, url, title, first_seen_ts) "
            "VALUES (?, ?, ?, strftime('%s','now'))",
            (item_id, url, title),
        )
        con.commit()

# ---------------- HTTP ----------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

def fetch_text(url: str) -> str:
    r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

def stable_id(title: str, url: str) -> str:
    s = (title.strip().lower() + "|" + url.strip()).encode("utf-8")
    return hashlib.sha256(s).hexdigest()

# ---------------- COUPON ----------------
def extract_coupon_code(url: str) -> str:
    try:
        q = parse_qs(urlparse(url).query)
        return (q.get("couponCode", [""])[0] or "").strip()
    except Exception:
        return ""

# ---------------- COUNTDOWN -> EXPIRY ----------------
def parse_countdown_to_expiry(text: str) -> Tuple[str, Optional[datetime]]:
    """
    Works on raw HTML OR visible text.
    Matches:
      - "30 days left at this price!"
      - "5 hours left at this price!"
      - "12 minutes left at this price!"
      - "Ends in 2 days"
      - "Expires in 3 hours"
    Returns (countdown_text, expiry_dt_utc).
    """
    t = re.sub(r"\s+", " ", text).strip()

    patterns = [
        r"(\d+)\s+(day|days|hour|hours|minute|minutes)\s+left\s+at\s+this\s+price!?",
        r"(?:ends|expires)\s+in\s+(\d+)\s+(day|days|hour|hours|minute|minutes)!?",
    ]

    for p in patterns:
        m = re.search(p, t, flags=re.IGNORECASE)
        if not m:
            continue

        n = int(m.group(1))
        unit = m.group(2).lower()

        now = datetime.now(timezone.utc)
        if "day" in unit:
            expiry = now + timedelta(days=n)
        elif "hour" in unit:
            expiry = now + timedelta(hours=n)
        else:
            expiry = now + timedelta(minutes=n)

        countdown = m.group(0).strip()
        countdown = countdown[0].upper() + countdown[1:]
        return countdown, expiry

    return "", None

def format_discord_dt(dt_utc: datetime) -> str:
    unix = int(dt_utc.timestamp())
    return f"<t:{unix}:F>"

def format_mmddyyyy(dt_utc: datetime) -> str:
    # Windows-safe date format
    return dt_utc.astimezone(timezone.utc).strftime("%m/%d/%Y")

# ---------------- PRICE EXTRACTION (best-effort) ----------------
def extract_prices_from_udemy_html(html: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Best-effort: return (list_price, current_price).
    current_price may be 'Free' if found.
    NOTE: Udemy pricing is region/login dependent, so this is heuristic.
    """
    text = re.sub(r"\s+", " ", html)

    # If "Free" appears, treat current price as Free
    current_price = "Free" if re.search(r"\bFree\b", text, re.IGNORECASE) else None

    # Currency amounts like $19.99 €9.99 £12.99
    money = r"[$€£]\s?\d+(?:[.,]\d{2})?"

    # Try find two prices close together (original + discounted)
    m = re.search(rf"({money}).{{0,120}}({money})", text)
    if m:
        p1 = m.group(1).replace(" ", "")
        p2 = m.group(2).replace(" ", "")
        # guess which is larger
        try:
            n1 = float(re.sub(r"[^0-9.]", "", p1).replace(",", "."))
            n2 = float(re.sub(r"[^0-9.]", "", p2).replace(",", "."))
            list_price = p1 if n1 >= n2 else p2
            disc_price = p2 if n1 >= n2 else p1
            if current_price == "Free":
                return list_price, "Free"
            return list_price, disc_price
        except Exception:
            pass

    # If only one price appears, treat as list price
    m1 = re.search(rf"({money})", text)
    if m1:
        return m1.group(1).replace(" ", ""), current_price

    return None, current_price

# ---------------- UDEMY DETAILS ----------------
def fetch_udemy_details(udemy_url: str) -> Tuple[str, str, Optional[datetime], str, Optional[str], Optional[str]]:
    """
    Returns (title, countdown_text, expiry_dt_utc, image_url, list_price, current_price)
    """
    try:
        r = SESSION.get(udemy_url, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, "lxml")

        # Title
        title = ""
        ogt = soup.find("meta", property="og:title")
        if ogt and ogt.get("content"):
            title = ogt["content"].strip()
        if not title and soup.title and soup.title.string:
            t = soup.title.string.strip()
            t = re.sub(r"\s*\|\s*Udemy\s*$", "", t, flags=re.IGNORECASE)
            title = t.strip()

        # Big image (og:image)
        image_url = ""
        ogi = soup.find("meta", property="og:image")
        if ogi and ogi.get("content"):
            image_url = ogi["content"].strip()

        # Prices (best-effort)
        list_price, current_price = extract_prices_from_udemy_html(html)

        # Countdown + expiry: try raw HTML first (sometimes hidden), then visible text
        countdown_text, expiry_dt = parse_countdown_to_expiry(html)
        if not countdown_text:
            page_text = soup.get_text(" ", strip=True)
            countdown_text, expiry_dt = parse_countdown_to_expiry(page_text)

        return title, countdown_text, expiry_dt, image_url, list_price, current_price

    except Exception:
        return "", "", None, "", None, None

# ---------------- UDEMYFREEBIES PARSING ----------------
def parse_udemyfreebies_home(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    detail_urls: List[str] = []

    for a in soup.select("a[href]"):
        txt = a.get_text(" ", strip=True).lower()
        if "coupon detail" not in txt:
            continue
        href = (a.get("href") or "").strip()
        if href:
            detail_urls.append(urljoin(base_url, href))

    # dedupe while preserving order
    seen_set = set()
    out = []
    for u in detail_urls:
        if u not in seen_set:
            out.append(u)
            seen_set.add(u)
    return out

def udemy_url_from_detail(detail_html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(detail_html, "lxml")

    preferred_texts = [
        "go to course", "go to udemy", "enroll now", "get coupon", "take this course", "enrol now"
    ]

    candidates: List[str] = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        full = urljoin(base_url, href)
        text = a.get_text(" ", strip=True).lower()

        # Direct Udemy link
        if "udemy.com" in urlparse(full).netloc:
            candidates.append(full)
            continue

        # Likely outbound button by text
        if any(t in text for t in preferred_texts):
            candidates.append(full)

        # Extra heuristic: redirect/out links (helps if button text changes)
        if any(k in full.lower() for k in ["redirect", "out", "coupon"]):
            candidates.append(full)

    # Try candidates and follow redirects until we hit Udemy course URL
    for url in candidates[:12]:
        try:
            r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
            r.raise_for_status()
            final_url = r.url
            if "udemy.com" in urlparse(final_url).netloc and "/course/" in final_url:
                return final_url
        except Exception:
            continue

    # Last resort: regex scan
    m = re.search(r"https?://[^\s\"'>]*udemy\.com/course/[^\s\"'>]+", detail_html)
    if m:
        return m.group(0)

    return None

# ---------------- FREE STUFF STYLE FORMATTING ----------------
def format_price_line(list_price: Optional[str], current_price: Optional[str]) -> str:
    """
    Returns string like: 'Free ~~$19.99~~ 100% off'
    """
    cur = current_price or "Free"
    parts = [cur]

    if list_price:
        # If it's free, show strike-through list + 100% off
        if cur.lower() == "free":
            parts.append(f"~~{list_price}~~")
            parts.append("100% off")
        else:
            # If not free, still show strike-through list if different
            if list_price != cur:
                parts.append(f"~~{list_price}~~")

    return " ".join(parts).strip()

# ---------------- DISCORD EMBED (WEBHOOK) ----------------
def post_embed_to_discord(
    title: str,
    udemy_url: str,
    coupon: str,
    countdown_text: str,
    expiry_dt: Optional[datetime],
    image_url: str,
    list_price: Optional[str],
    current_price: Optional[str],
) -> None:
    # FreeStuff-style: minimal, clean, big image
    price_line = format_price_line(list_price, current_price)

    # "Free until <date>" line if we have an estimated expiry
    until_line = ""
    if expiry_dt:
        until_line = f"Free until {format_mmddyyyy(expiry_dt)}"
    elif countdown_text:
        until_line = countdown_text

    desc_lines = []
    if price_line:
        desc_lines.append(f"**{price_line}**")
    if until_line:
        desc_lines.append(f"⏳ {until_line}")

    # Fields (small metadata-like)
    fields = []
    if coupon:
        fields.append({"name": "Coupon", "value": f"`{coupon}`", "inline": True})
    if expiry_dt:
        fields.append({"name": "Ends", "value": f"🔥 {format_discord_dt(expiry_dt)}", "inline": True})

    embed = {
        "title": title,
        "url": udemy_url,
        "description": "\n".join(desc_lines) if desc_lines else "Free deal detected.",
        "fields": fields,
        "footer": {"text": "via udemyfreebies.com"},
    }

    # BIG image like your Steam sample
    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "content": "",  # FreeStuff-style: no extra text above the embed
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    r = SESSION.post(WEBHOOK_URL, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

# ---------------- RUN LOOP ----------------
def run_once() -> int:
    listing_html = fetch_text(SOURCE_URL)
    parsed = urlparse(SOURCE_URL)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"

    detail_urls = parse_udemyfreebies_home(listing_html, base_url)

    posted = 0
    for detail_url in detail_urls[:MAX_DETAILS_PER_RUN]:
        try:
            detail_html = fetch_text(detail_url)
        except Exception:
            continue

        udemy_url = udemy_url_from_detail(detail_html, base_url)
        if not udemy_url:
            continue

        coupon = extract_coupon_code(udemy_url)

        title, countdown_text, expiry_dt, image_url, list_price, current_price = fetch_udemy_details(udemy_url)
        if not title:
            title = "Udemy course"

        # Dedupe: include coupon code so a new coupon for same course can alert again
        dedupe_key = udemy_url + "|" + (coupon or "")
        item_id = stable_id(title, dedupe_key)
        if seen(item_id):
            continue

        try:
            post_embed_to_discord(
                title=title,
                udemy_url=udemy_url,
                coupon=coupon,
                countdown_text=countdown_text,
                expiry_dt=expiry_dt,
                image_url=image_url,
                list_price=list_price,
                current_price=current_price,
            )
            mark_seen(item_id, udemy_url, title)
            posted += 1
        except Exception as e:
            print("[POST ERROR]", e)

    print(f"Found {len(detail_urls)} coupon detail links, posted {posted}.")
    return posted

def main() -> None:
    init_db()
    print("Starting UdemyFreebies alerts (FreeStuff-style embed + price formatting)")
    print("SOURCE_URL:", SOURCE_URL)
    print("Polling every", POLL_SECONDS, "seconds")

    while True:
        try:
            run_once()
        except Exception as e:
            print("[ERROR]", e)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()