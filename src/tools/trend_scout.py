"""Agent 1 Pre-Scout (0-Token): Global Everyday Sci-Tech Trend Detector + Idea Bank Fallback.

Priority 1 (Viral-First):
  Scans live English RSS/news feeds for trending everyday science & applied technology topics
  across: gadgets, engineering, materials, energy, and biotech.

Priority 2 (Automatic Fallback):
  If no fresh everyday-relatable sci-tech topic passes the relevance filter (or all are already covered),
  automatically pulls balanced unused ideas from `data/idea_bank.json` via StudioMemory.
"""
from __future__ import annotations

import email.utils
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import urllib3.util.connection as _urllib3_conn

from src.brand_config import get_brand_name
from src.memory import StudioMemory

_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

DISCIPLINE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gadgets": (
        "smartphone", "iphone", "android", "oled", "display", "screen", "chip", "processor",
        "battery", "fast charging", "usb", "wifi", "bluetooth", "nfc", "earbuds", "camera",
        "sensor", "touchscreen", "ai device", "wireless", "lidar", "radar", "speaker",
    ),
    "engineering": (
        "engineering", "bridge", "elevator", "fridge", "refrigerator", "air conditioner", "hvac",
        "microwave", "induction", "traffic", "robot", "drone", "3d print", "factory", "turbine",
        "pump", "hydraulic", "acoustic", "skyscraper", "tunnel", "road", "asphalt",
    ),
    "materials": (
        "material", "glass", "gorilla glass", "polymer", "plastic", "ceramic", "steel", "alloy",
        "graphene", "nanotech", "coating", "hydrophobic", "teflon", "adhesive", "foam",
        "concrete", "self-healing", "fabric", "fiber", "titanium", "silicon", "recycling",
    ),
    "energy": (
        "solar", "battery", "electric vehicle", "ev", "grid", "wind", "fusion", "nuclear",
        "charger", "power line", "grid", "heat pump", "thermal", "cooling", "airplane",
        "aerodynamics", "fuel", "hydrogen", "kinetic", "regenerative", "voltage",
    ),
    "biotech": (
        "smartwatch", "wearable", "sleep", "blue light", "melatonin", "filter", "water",
        "purifier", "air quality", "noise", "hearing", "vision", "vr", "headset", "eye",
        "nutrition", "cooking", "air fryer", "caffeine", "brain", "health tech", "biometric",
    ),
}

EVERYDAY_RELATABILITY_KEYWORDS: tuple[str, ...] = (
    "phone", "smartphone", "screen", "battery", "charge", "wifi", "earbuds", "camera",
    "car", "ev", "brake", "airplane", "flight", "traffic", "road", "bridge", "elevator",
    "kitchen", "fridge", "microwave", "stove", "pan", "coffee", "water", "air", "home",
    "house", "sleep", "eye", "brain", "heat", "cold", "cool", "glass", "plastic", "metal",
    "shoe", "clothes", "paint", "light", "led", "solar", "watch", "wearable", "vr",
)

GLOBAL_VIRAL_STYLE_BENCHMARKS: list[str] = [
    "@Veritasium: Counter-intuitive everyday mystery + instant experiment ('Why nobody can build a faster...')",
    "@CleoAbram: Huge tech breakthroughs translated directly into 'what this changes in your daily life'",
    "@ZackDFilms: Hyper-visual 3-second problem hook showing what happens inside everyday objects",
    "@BranchEducation: Zooming inside microchips, screens, and signals step by step with crystal-clear analogies",
    "@MKBHD Shorts: Crisp 1-sentence gadget truth that busts a common consumer myth",
]

FEEDS: list[dict[str, str]] = [
    {
        "name": "ScienceDaily Technology",
        "url": "https://www.sciencedaily.com/rss/top/technology.xml",
        "lang": "en",
    },
    {
        "name": "Phys.org Technology & Engineering",
        "url": "https://phys.org/rss-feed/technology-news/",
        "lang": "en",
    },
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
        "lang": "en",
    },
    {
        "name": "IEEE Spectrum",
        "url": "https://spectrum.ieee.org/feeds/feed.rss",
        "lang": "en",
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "lang": "en",
    },
    {
        "name": "ScienceDaily Top Science",
        "url": "https://www.sciencedaily.com/rss/top/science.xml",
        "lang": "en",
    },
]


def _classify_discipline(text: str) -> tuple[str | None, int]:
    low = text.lower()
    best_disc = None
    best_hits = 0
    for disc, kws in DISCIPLINE_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in low)
        if hits > best_hits:
            best_hits = hits
            best_disc = disc
    return best_disc, best_hits


def _strip_html(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", txt).strip()


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_viral_scitech_candidates(
    lookback_hours: int = 72,
    max_items: int = 8,
    timeout_sec: int = 10,
) -> list[dict[str, Any]]:
    """Fetch fresh everyday sci-tech headlines across RSS feeds (0 AI tokens)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    headers = {"User-Agent": f"{get_brand_name()}Studio/1.0 (everyday-scitech-scout)"}
    collected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for feed in FEEDS:
        try:
            resp = requests.get(feed["url"], headers=headers, timeout=timeout_sec)
            if resp.status_code != 200 or not resp.text.strip():
                continue
            root = ET.fromstring(resp.text)
        except Exception:
            continue

        items = root.findall(".//item")
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items[:20]:
            title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            link_el = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
            desc_el = (
                item.find("description")
                or item.find("summary")
                or item.find("{http://www.w3.org/2005/Atom}summary")
            )
            pub_el = (
                item.find("pubDate")
                or item.find("published")
                or item.find("updated")
                or item.find("{http://www.w3.org/2005/Atom}updated")
            )

            title = _strip_html(title_el.text if title_el is not None and title_el.text else "")
            if not title or len(title) < 12:
                continue
            norm_t = re.sub(r"[^a-z0-9]", "", title.lower())[:48]
            if norm_t in seen_titles:
                continue

            link = ""
            if link_el is not None:
                link = (link_el.text or "").strip() or link_el.attrib.get("href", "").strip()
            summary = _strip_html(desc_el.text if desc_el is not None and desc_el.text else "")[:280]
            pub_dt = _parse_pub_date(pub_el.text if pub_el is not None else None)
            if pub_dt and pub_dt < cutoff:
                continue

            blob = f"{title} {summary}".lower()
            disc, hits = _classify_discipline(blob)
            if not disc or hits < 1:
                continue
            everyday_hits = sum(1 for kw in EVERYDAY_RELATABILITY_KEYWORDS if kw in blob)

            seen_titles.add(norm_t)
            collected.append(
                {
                    "feed": feed["name"],
                    "lang": feed["lang"],
                    "discipline": disc,
                    "relevance_hits": hits + everyday_hits * 2,
                    "everyday_relatable": everyday_hits > 0,
                    "title": title,
                    "summary": summary,
                    "url": link,
                    "published_utc": pub_dt.isoformat() if pub_dt else "",
                }
            )

    collected.sort(
        key=lambda x: (x["everyday_relatable"], x["relevance_hits"], x["published_utc"]),
        reverse=True,
    )
    return collected[:max_items]


def build_agent1_input_brief(
    base_dir: Path,
    lookback_hours: int = 72,
    min_viral_matches: int = 2,
    is_tech_radar: bool = False,
) -> dict[str, Any]:
    """Assemble compact context for Agent 1: viral everyday sci-tech trends + balanced fallback ideas."""
    memory = StudioMemory(base_dir)
    past_topics = memory.list_past_topics(limit=35)
    past_titles_lower = {p["title"].lower() for p in past_topics}
    past_slugs = {p["slug"] for p in past_topics}

    raw_viral = fetch_viral_scitech_candidates(lookback_hours=lookback_hours, max_items=10)
    viral_filtered = [
        v for v in raw_viral if v["title"].lower() not in past_titles_lower
    ]

    fallback_ideas = memory.get_balanced_unused_ideas(count=5)
    discipline_balance = memory.discipline_counts()

    use_viral = len(viral_filtered) >= min_viral_matches or (is_tech_radar and len(viral_filtered) >= 1)
    return {
        "recommended_mode": "viral_trend" if use_viral else "idea_bank_fallback",
        "section_focus": "TECH_RADAR (Weekend Latest Sci-Tech Breakthroughs)" if is_tech_radar else "EVERYDAY_TECH (Daily Life Science & Technology)",
        "viral_candidates": viral_filtered[:6],
        "idea_bank_fallback_candidates": fallback_ideas,
        "discipline_balance_counts": discipline_balance,
        "global_viral_style_benchmarks": GLOBAL_VIRAL_STYLE_BENCHMARKS,
        "already_covered_titles": [p["title"] for p in past_topics[:20]],
        "already_covered_slugs": list(past_slugs)[:20],
    }
