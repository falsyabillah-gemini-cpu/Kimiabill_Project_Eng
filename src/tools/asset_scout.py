"""Agent 6 Tool: Resilient 3-Tier Visual Waterfall + 1-Call Contact-Sheet Vision QA.

Eliminates Pexels free-tier dependency & footage-induced build failures:
  Tier 1 (Real Stock Video):
    1. PIXABAY_API_KEY (primary, with 24h local query cache per Pixabay ToS)
    2. COVERR_API_KEY (secondary curated B-roll; gracefully skipped if unset)
    3. Keyless NASA Image & Video Library (prioritized for physics/optics/energy/materials/space)
    4. PEXELS_API_KEY (strictly optional; auto-disabled on 401/403/429 or unset)
    5. Wikimedia Commons (strict last resort: blocks lecture/presentation/slide/intro/logo/university/seminar/arduino/demo or >3m, trims 2.0s ends)
  Tier 2 (High-Res Portrait Photo + Anti-Jitter FFmpeg Ken Burns):
    1. UNSPLASH_ACCESS_KEY (portrait + links.download_location guideline ping)
    2. PIXABAY_API_KEY photo (vertical photo with 24h cache)
    3. Keyless Openverse CC0/PDM images
  Tier 3 (AI-Generated 9:16 Scene + Ken Burns):
    - Gemini Imagen / Flash Image / Cloudflare Workers AI (zero text/watermark/logo)
    - Zero Hard-Crash Policy: deterministic photoreal backdrop Ken Burns only if all image APIs fail/429.

Validates ALL candidate shots in ONE single Vision API Contact-Sheet call:
  - Shot #1 (Hook 0-3s): strict >= 7.5/10
  - Shot #2..N: calibrated >= 6.0/10 (still blocking watermarks, foreign slides, off-topic scenes)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
import urllib3.util.connection as _urllib3_conn
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from src.brand_config import BASE_DIR, get_brand_name, get_gdrive_root
from src.memory import StudioMemory
from src.state import Agent6Output, SceneAsset, SceneSpec

_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

USER_AGENT = f"{get_brand_name()}Studio/2.0 (everyday-scitech-bot)"
CACHE_PATH = BASE_DIR / "data" / "media_search_cache.json"
CACHE_TTL_SEC = 24 * 3600

_PEXELS_DISABLED_THIS_RUN = False

DISALLOWED_TITLE_TOKENS: tuple[str, ...] = (
    "animation", "animated", "cartoon", "3d render", "blender", "simulation", "simulated",
    "diagram", "lecture", "presentation", "interview", "talk", "podcast", "webinar",
    "slideshow", "trailer", "logo", "intro", "outro", "music video", "album", "karaoke",
    "gameplay", "screen recording", "screencast", "wikipedia", "wp25", "wikimedia",
    "wikimania", "anthem", "caption", "billboard", "commercial", "promo", "donation",
    "scp-", "tutorial", "university", "seminar", "arduino", "demo", "conference",
    "slide", "powerpoint", "ukulele", "guitar", "horseback", "flamingo", "cctv",
    "wallet theft", "hubblecast", "esocast", "graduation", "recipe",
)

UNSAFE_TOKENS: tuple[str, ...] = (
    "nude", "nudity", "naked", "topless", "nsfw", "porn", "erotic", "sex", "sexual",
    "genital", "penis", "vagina", "breast", "nipple", "masturbat", "orgasm", "fetish",
    "bdsm", "bondage", "lingerie", "bikini", "stripper", "striptease", "intercourse",
    "condom", "explicit", "adult film", "xxx", "gore", "corpse", "dead body", "autopsy",
    "beheading", "execution", "suicide", "murder", "shoot", "shooting", "knife-wielding",
    "suspect", "gun", "weapon", "war crime", "drug use", "injecting", "surgery",
    "pregnan", "childbirth", "dancing", "pole dance", "fashion show", "model posing",
)


def _is_family_safe_text(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    blob = re.sub(r"<[^>]+>", " ", blob)
    return not any(tok in blob for tok in UNSAFE_TOKENS)


def _is_clean_real_title(title: str, extra_negative: Optional[list[str]] = None) -> bool:
    low = (title or "").lower()
    if any(tok in low for tok in DISALLOWED_TITLE_TOKENS):
        return False
    if extra_negative:
        for neg in extra_negative:
            n_clean = (neg or "").strip().lower()
            if len(n_clean) >= 3 and n_clean in low:
                return False
    return True


@dataclass
class VideoCandidate:
    source: str
    title: str
    download_url: str
    page_url: str
    license: str
    duration_sec: float
    width: int
    height: int
    tier: str = "tier_1_video"  # tier_1_video | tier_2_photo_ken_burns | tier_3_ai_scene_ken_burns
    thumb_url: Optional[str] = None
    unsplash_download_location: Optional[str] = None
    needs_trim_ends: bool = False


def probe_video(path: Path) -> Optional[dict]:
    try:
        raw = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name:format=duration",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(raw)
        streams = data.get("streams", [])
        if not streams:
            return None
        st = streams[0]
        dur = float(data.get("format", {}).get("duration") or 0.0)
        return {
            "width": int(st.get("width", 0)),
            "height": int(st.get("height", 0)),
            "codec": st.get("codec_name", ""),
            "duration": dur,
        }
    except Exception:
        return None


# === 24-Hour Local Search Cache (Pixabay / Openverse Compliance & Speed) ===

def _load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        now = time.time()
        pruned = {
            k: v for k, v in cache.items()
            if isinstance(v, dict) and (now - float(v.get("ts", 0))) < CACHE_TTL_SEC
        }
        if len(pruned) > 250:
            items = sorted(pruned.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:250]
            pruned = dict(items)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(pruned, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _cache_get(namespace: str, query: str) -> Optional[list[dict[str, Any]]]:
    key = hashlib.sha256(f"{namespace}:{query.strip().lower()}".encode("utf-8")).hexdigest()[:24]
    entry = _load_cache().get(key)
    if not isinstance(entry, dict):
        return None
    if (time.time() - float(entry.get("ts", 0))) > CACHE_TTL_SEC:
        return None
    return entry.get("items")


def _cache_set(namespace: str, query: str, items: list[dict[str, Any]]) -> None:
    key = hashlib.sha256(f"{namespace}:{query.strip().lower()}".encode("utf-8")).hexdigest()[:24]
    cache = _load_cache()
    cache[key] = {"ts": time.time(), "items": items}
    _save_cache(cache)


# === TIER 1: Real Stock Video Providers (No Pexels Dependency) ===

def search_pixabay(query: str, per_page: int = 8) -> list[VideoCandidate]:
    """Primary Tier 1: Pixabay Video API with 24h local cache (per Pixabay ToS)."""
    api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return []
    cached = _cache_get("pixabay_video", query)
    hits: list[dict[str, Any]]
    if cached is not None:
        hits = cached
    else:
        try:
            resp = requests.get(
                "https://pixabay.com/api/videos/",
                headers={"User-Agent": USER_AGENT},
                params={
                    "key": api_key,
                    "q": query,
                    "per_page": per_page,
                    "safesearch": "true",
                    "video_type": "film",
                },
                timeout=18,
            )
            if resp.status_code != 200:
                return []
            hits = resp.json().get("hits", [])
            _cache_set("pixabay_video", query, hits)
        except requests.RequestException:
            return []

    out: list[VideoCandidate] = []
    for v in hits:
        dur = float(v.get("duration") or 0)
        if dur < 3.0:
            continue
        tags = str(v.get("tags") or "")
        if not _is_clean_real_title(tags) or not _is_family_safe_text(tags):
            continue
        videos = v.get("videos") or {}
        best = None
        for tier in ("medium", "large", "small"):
            cand_stream = videos.get(tier) or {}
            if cand_stream.get("url") and int(cand_stream.get("height") or 0) >= 480:
                best = cand_stream
                break
        if best is None:
            for tier in ("medium", "large", "small", "tiny"):
                cand_stream = videos.get(tier) or {}
                if cand_stream.get("url"):
                    best = cand_stream
                    break
        if not best:
            continue
        out.append(
            VideoCandidate(
                source="pixabay",
                title=f"Pixabay #{v.get('id')} - {tags or query}"[:120],
                download_url=best["url"],
                page_url=v.get("pageURL") or best["url"],
                license="Pixabay Content License (Free)",
                duration_sec=dur,
                width=int(best.get("width") or 1280),
                height=int(best.get("height") or 720),
                tier="tier_1_video",
            )
        )
    return out


def search_coverr(query: str, limit: int = 6) -> list[VideoCandidate]:
    """Secondary Tier 1: Coverr Curated Stock Video API (gracefully skipped if COVERR_API_KEY unset)."""
    api_key = os.environ.get("COVERR_API_KEY", "").strip()
    if not api_key:
        return []
    cached = _cache_get("coverr_video", query)
    items: list[dict[str, Any]]
    if cached is not None:
        items = cached
    else:
        try:
            resp = requests.get(
                "https://api.coverr.co/videos",
                headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
                params={"query": query, "page_size": limit},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
            items = payload.get("hits") or payload.get("videos") or []
            if isinstance(payload, list):
                items = payload
            _cache_set("coverr_video", query, items[:limit])
        except requests.RequestException:
            return []

    out: list[VideoCandidate] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("description") or query)
        tags_raw = item.get("tags") or []
        tags_txt = " ".join(str(t) for t in tags_raw) if isinstance(tags_raw, list) else str(tags_raw)
        if not _is_clean_real_title(f"{title} {tags_txt}") or not _is_family_safe_text(title, tags_txt):
            continue
        urls = item.get("urls") or {}
        mp4 = (
            urls.get("mp4_download")
            or urls.get("mp4")
            or item.get("mp4_url")
            or item.get("url")
        )
        if not mp4 or not isinstance(mp4, str):
            continue
        dur = float(item.get("duration") or 8.0)
        if dur < 3.0:
            continue
        out.append(
            VideoCandidate(
                source="coverr",
                title=f"Coverr - {title}"[:120],
                download_url=mp4,
                page_url=str(item.get("page_url") or mp4),
                license="Coverr Free License",
                duration_sec=dur,
                width=int(item.get("width") or 1080),
                height=int(item.get("height") or 1920),
                tier="tier_1_video",
            )
        )
    return out


def search_nasa_videos(query: str, limit: int = 5) -> list[VideoCandidate]:
    """Keyless Tier 1: NASA Images & Video API (Public Domain HD physics/optics/energy/materials)."""
    cached = _cache_get("nasa_video", query)
    items: list[dict[str, Any]]
    if cached is not None:
        items = cached
    else:
        try:
            resp = requests.get(
                "https://images-api.nasa.gov/search",
                headers={"User-Agent": USER_AGENT},
                params={"q": query, "media_type": "video"},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            items = resp.json().get("collection", {}).get("items", [])[: limit * 2]
            _cache_set("nasa_video", query, items)
        except requests.RequestException:
            return []

    out: list[VideoCandidate] = []
    for item in items:
        if len(out) >= limit:
            break
        data_list = item.get("data") or []
        if not data_list:
            continue
        data = data_list[0]
        title = str(data.get("title") or "").strip()
        desc = str(data.get("description") or "")
        nasa_id = str(data.get("nasa_id") or "").strip()
        if not title or not _is_clean_real_title(title) or not _is_family_safe_text(title, desc):
            continue
        collection_url = item.get("href")
        if not collection_url:
            continue
        try:
            col_resp = requests.get(collection_url, headers={"User-Agent": USER_AGENT}, timeout=12)
            if col_resp.status_code != 200:
                continue
            asset_urls = col_resp.json()
        except Exception:
            continue
        mp4_urls = [u for u in asset_urls if isinstance(u, str) and u.lower().endswith(".mp4")]
        if not mp4_urls:
            continue
        mp4_url = next(
            (u for sfx in ("~medium.mp4", "~small.mp4", "~orig.mp4") for u in mp4_urls if u.lower().endswith(sfx)),
            mp4_urls[0],
        )
        out.append(
            VideoCandidate(
                source="nasa",
                title=f"NASA - {title}"[:120],
                download_url=mp4_url,
                page_url=f"https://images.nasa.gov/details/{nasa_id}" if nasa_id else mp4_url,
                license="NASA Public Domain (No Copyright)",
                duration_sec=10.0,
                width=1280,
                height=720,
                tier="tier_1_video",
            )
        )
    return out


def search_pexels(query: str, per_page: int = 6, orientation: Optional[str] = "portrait") -> list[VideoCandidate]:
    """Strictly optional Tier 1 legacy helper: skips silently if unset or returning 401/403/429."""
    global _PEXELS_DISABLED_THIS_RUN
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if _PEXELS_DISABLED_THIS_RUN or not api_key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page}
    if orientation:
        params["orientation"] = orientation
    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
            params=params,
            timeout=15,
        )
    except requests.RequestException:
        return []
    if resp.status_code in (401, 403, 429):
        _PEXELS_DISABLED_THIS_RUN = True
        print(f"[asset_scout] Pexels HTTP {resp.status_code} — disabling Pexels for remainder of run.")
        return []
    if resp.status_code != 200:
        return []
    out: list[VideoCandidate] = []
    for v in resp.json().get("videos", []):
        dur = float(v.get("duration", 0))
        if dur < 3.0:
            continue
        files = sorted(
            [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: abs((f.get("height") or 0) - 1080),
        )
        if not files:
            continue
        best = files[0]
        out.append(
            VideoCandidate(
                source="pexels",
                title=f"Pexels #{v.get('id')} - {query}",
                download_url=best["link"],
                page_url=v.get("url", best["link"]),
                license="Pexels License (Free)",
                duration_sec=dur,
                width=int(best.get("width") or 720),
                height=int(best.get("height") or 1280),
                tier="tier_1_video",
            )
        )
    return out


def search_wikimedia_videos(query: str, limit: int = 8) -> list[VideoCandidate]:
    """Strict last-resort Tier 1 fallback: blocks lectures/presentations/slides/demos & >3m, trims 2.0s ends."""
    headers = {"User-Agent": USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:video",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "videoinfo",
        "viprop": "url|size|mime|extmetadata",
        "viurlwidth": "720",
    }
    try:
        resp = requests.get("https://commons.wikimedia.org/w/api.php", headers=headers, params=params, timeout=18)
        if resp.status_code != 200:
            return []
        pages = resp.json().get("query", {}).get("pages", {})
    except Exception:
        return []

    out: list[VideoCandidate] = []
    for page in pages.values():
        title = page.get("title", "")
        meta = page.get("extmetadata", {})
        meta_text = " ".join(
            str(meta.get(k, {}).get("value", ""))
            for k in ("Categories", "ImageDescription", "ObjectName", "Restrictions")
        )
        if not _is_clean_real_title(f"{title} {meta_text}"):
            continue
        if not _is_family_safe_text(title, meta_text):
            continue
        vis = page.get("videoinfo", [])
        if not vis:
            continue
        vi = vis[0]
        mime = vi.get("mime", "")
        if not mime.startswith("video/") and "ogg" not in mime:
            continue
        dur = float(vi.get("duration") or 0.0)
        w = int(vi.get("width") or 0)
        h = int(vi.get("height") or 0)
        # Strict duration <= 180s (3 min) & >= 5s (so 2.0s trim on both ends leaves >= 1s)
        if dur < 5.5 or dur > 180.0 or w < 480 or h < 360:
            continue
        url = vi.get("url")
        if not url:
            continue
        lic = re.sub(r"<[^>]+>", "", str(meta.get("LicenseShortName", {}).get("value", "Wikimedia Commons"))).strip()
        out.append(
            VideoCandidate(
                source="wikimedia_commons",
                title=title.replace("File:", "")[:120],
                download_url=url,
                page_url=vi.get("descriptionurl", url),
                license=lic,
                duration_sec=dur,
                width=w,
                height=h,
                tier="tier_1_video",
                needs_trim_ends=True,
            )
        )
    out.sort(key=lambda c: (0 if 6 <= c.duration_sec <= 60 else 1, -min(c.width, 1920)))
    return out


# === TIER 2: Portrait Photos + Anti-Jitter Ken Burns ===

def search_unsplash_portrait(query: str, per_page: int = 6) -> list[VideoCandidate]:
    """Tier 2 Primary: Unsplash vertical photos (Client-ID auth + download_location trigger)."""
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not key:
        return []
    cached = _cache_get("unsplash_photo", query)
    results: list[dict[str, Any]]
    if cached is not None:
        results = cached
    else:
        try:
            resp = requests.get(
                "https://api.unsplash.com/search/photos",
                headers={
                    "Authorization": f"Client-ID {key}",
                    "Accept-Version": "v1",
                    "User-Agent": USER_AGENT,
                },
                params={
                    "query": query,
                    "orientation": "portrait",
                    "per_page": per_page,
                    "content_filter": "high",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("results", [])
            _cache_set("unsplash_photo", query, results)
        except requests.RequestException:
            return []

    out: list[VideoCandidate] = []
    for item in results:
        alt = str(item.get("alt_description") or item.get("description") or query)
        if not _is_clean_real_title(alt) or not _is_family_safe_text(alt):
            continue
        urls = item.get("urls") or {}
        img_url = urls.get("regular") or urls.get("full") or urls.get("small")
        if not img_url:
            continue
        links = item.get("links") or {}
        out.append(
            VideoCandidate(
                source="unsplash",
                title=f"Unsplash - {alt}"[:120],
                download_url=img_url,
                page_url=links.get("html") or img_url,
                license="Unsplash License (Free)",
                duration_sec=8.0,
                width=int(item.get("width") or 1080),
                height=int(item.get("height") or 1920),
                tier="tier_2_photo_ken_burns",
                unsplash_download_location=links.get("download_location"),
            )
        )
    return out


def _trigger_unsplash_download(cand: VideoCandidate) -> None:
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not key or not cand.unsplash_download_location:
        return
    try:
        requests.get(
            cand.unsplash_download_location,
            headers={"Authorization": f"Client-ID {key}", "User-Agent": USER_AGENT},
            timeout=8,
        )
    except Exception:
        pass


def search_pixabay_photo(query: str, per_page: int = 6) -> list[VideoCandidate]:
    """Tier 2 Secondary: Pixabay vertical photo API with 24h local cache."""
    api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return []
    cached = _cache_get("pixabay_photo", query)
    hits: list[dict[str, Any]]
    if cached is not None:
        hits = cached
    else:
        try:
            resp = requests.get(
                "https://pixabay.com/api/",
                headers={"User-Agent": USER_AGENT},
                params={
                    "key": api_key,
                    "q": query,
                    "image_type": "photo",
                    "orientation": "vertical",
                    "safesearch": "true",
                    "per_page": per_page,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            hits = resp.json().get("hits", [])
            _cache_set("pixabay_photo", query, hits)
        except requests.RequestException:
            return []

    out: list[VideoCandidate] = []
    for item in hits:
        tags = str(item.get("tags") or query)
        if not _is_clean_real_title(tags) or not _is_family_safe_text(tags):
            continue
        url = item.get("largeImageURL") or item.get("webformatURL")
        if not url:
            continue
        out.append(
            VideoCandidate(
                source="pixabay_photo",
                title=f"Pixabay Photo #{item.get('id')} - {tags}"[:120],
                download_url=url,
                page_url=item.get("pageURL") or url,
                license="Pixabay Content License (Free)",
                duration_sec=8.0,
                width=int(item.get("imageWidth") or 1080),
                height=int(item.get("imageHeight") or 1600),
                tier="tier_2_photo_ken_burns",
            )
        )
    return out


def search_openverse_photo(query: str, limit: int = 5) -> list[VideoCandidate]:
    """Tier 2 Tertiary (Keyless): Openverse CC0 / Public Domain images."""
    cached = _cache_get("openverse_photo", query)
    results: list[dict[str, Any]]
    if cached is not None:
        results = cached
    else:
        try:
            resp = requests.get(
                "https://api.openverse.org/v1/images/",
                headers={"User-Agent": USER_AGENT},
                params={"q": query, "license": "cc0,pdm", "page_size": limit},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("results", [])
            _cache_set("openverse_photo", query, results)
        except requests.RequestException:
            return []

    out: list[VideoCandidate] = []
    for item in results:
        title = str(item.get("title") or query)
        if not _is_clean_real_title(title) or not _is_family_safe_text(title):
            continue
        url = item.get("url")
        w = int(item.get("width") or 0)
        h = int(item.get("height") or 0)
        if not url or (w and w < 640):
            continue
        out.append(
            VideoCandidate(
                source="openverse",
                title=f"Openverse - {title}"[:120],
                download_url=url,
                page_url=item.get("foreign_landing_url") or url,
                license=f"CC0 / PDM ({item.get('license', 'openverse')})",
                duration_sec=8.0,
                width=w or 1080,
                height=h or 1080,
                tier="tier_2_photo_ken_burns",
            )
        )
    return out


def _render_image_to_ken_burns_mp4(
    img_path: Path,
    dest_mp4: Path,
    duration_sec: float = 8.0,
    motion: str = "slow_zoom_in",
) -> Optional[dict]:
    """Render high-res still photo -> smooth anti-jitter 1080x1920 Ken Burns MP4 (30fps)."""
    try:
        fps = 30
        frames = max(90, int(round(duration_sec * fps)))
        # Pre-scale & center-crop to 2160x3840 (2x target) BEFORE zoompan so subpixel zoom stays silk-smooth (no jitter!)
        if motion == "slow_zoom_out":
            zp = f"zoompan=z='max(1.08-on/{frames}*0.08,1.0)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps}"
        elif motion == "pan_right":
            zp = f"zoompan=z='1.08':d={frames}:x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps}"
        else:
            zp = f"zoompan=z='min(zoom+0.08/{frames},1.08)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps}"

        vf = (
            "scale=2160:3840:force_original_aspect_ratio=increase:flags=lanczos,"
            "crop=2160:3840,"
            f"{zp},"
            "eq=contrast=1.05:saturation=1.08,"
            "format=yuv420p"
        )
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(img_path),
                "-vf", vf,
                "-t", f"{duration_sec:.2f}",
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(dest_mp4),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return probe_video(dest_mp4)
    except Exception:
        return None


# === TIER 3: AI-Generated 9:16 Scene + Ken Burns (Microscopic / Unmatchable Shots) ===

def _generate_ai_scene_image(scene: SceneSpec, out_jpg: Path) -> Optional[str]:
    """Generate 1 clean text-free 9:16 macro/photoreal visual via Gemini Imagen / Flash Image / Cloudflare."""
    prompt = (
        f"Photorealistic extreme macro studio photography for a science documentary depicting: {scene.visual_query}. "
        f"Context: {scene.narration[:140]}. Vertical 9:16 portrait composition, dramatic crisp lighting, sharp shallow "
        f"depth of field, STRICTLY NO TEXT, NO WORDS, NO LABELS, NO WATERMARKS, NO LOGOS."
    )

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        # 1. Try Imagen 3 predict endpoint (native 9:16)
        for im_model in ("imagen-3.0-generate-002", "imagen-3.0-generate-001"):
            try:
                resp = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{im_model}:predict?key={gemini_key}",
                    json={
                        "instances": [{"prompt": prompt}],
                        "parameters": {"sampleCount": 1, "aspectRatio": "9:16"},
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    preds = resp.json().get("predictions") or []
                    b64 = (preds[0] or {}).get("bytesBase64Encoded") if preds else None
                    if b64:
                        out_jpg.write_bytes(base64.b64decode(b64))
                        return f"gemini_{im_model}"
            except Exception:
                pass

        # 2. Try Gemini native image generation models
        for gm_model in ("gemini-2.5-flash-image", "gemini-2.0-flash-exp-image-generation"):
            try:
                resp = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{gm_model}:generateContent?key={gemini_key}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseModalities": ["IMAGE"]},
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    cands = resp.json().get("candidates") or []
                    parts = ((cands[0] or {}).get("content") or {}).get("parts") or [] if cands else []
                    for pt in parts:
                        inline = pt.get("inlineData") or pt.get("inline_data") or {}
                        if inline.get("data"):
                            out_jpg.write_bytes(base64.b64decode(inline["data"]))
                            return gm_model
            except Exception:
                pass

    # 3. Optional Cloudflare Workers AI Flux Schnell
    cf_acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    cf_tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if cf_acc and cf_tok:
        try:
            resp = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{cf_acc}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                headers={"Authorization": f"Bearer {cf_tok}"},
                json={"prompt": prompt, "num_steps": 4},
                timeout=60,
            )
            if resp.status_code == 200:
                img_b64 = (resp.json().get("result") or {}).get("image")
                if img_b64:
                    out_jpg.write_bytes(base64.b64decode(img_b64))
                    return "cloudflare_flux"
        except Exception:
            pass

    return None


def _draw_zero_crash_optical_backdrop(out_jpg: Path) -> None:
    """Deterministic text-free studio macro backdrop ONLY if external image generation APIs fail/429."""
    img = Image.new("RGB", (1080, 1920), (12, 20, 38))
    d = ImageDraw.Draw(img)
    for y in range(0, 1920, 6):
        c_b = int(38 + 34 * (1.0 - abs(y - 960) / 960.0))
        c_g = int(20 + 22 * (1.0 - abs(y - 960) / 960.0))
        d.rectangle([0, y, 1080, y + 6], fill=(12, c_g, c_b))
    for i in range(18):
        x = (i * 173 + 90) % 980 + 50
        y = (i * 281 + 140) % 1600 + 160
        r = 45 + (i % 4) * 32
        d.ellipse([x - r, y - r, x + r, y + r], outline=(42, 165, 225), width=3)
    img.save(out_jpg, quality=92)


# === Helpers: Flow Clip Bank + Download & Normalize ===

def list_flow_clip_bank() -> list[dict]:
    """Fetch Google AI Flow MP4 clips from <gdrive_root>/Flow_Clip_Bank via Apps Script webhook."""
    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        return []
    try:
        from src.tools.gdrive_sync import _normalize_webhook_url

        webhook_url = _normalize_webhook_url(raw_url)
        token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
        resp = requests.post(
            webhook_url,
            json={
                "action": "list_flow_clips",
                "token": token,
                "root_folder": get_gdrive_root(),
                "folder_name": "Flow_Clip_Bank",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("ok"):
            return []
        clips = data.get("clips") or []
        return [c for c in clips if isinstance(c, dict) and c.get("file_id") and c.get("filename")]
    except Exception:
        return []


def _download_raw_clip(candidate: VideoCandidate, raw_path: Path, max_download_mb: int = 32) -> Optional[dict]:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    try:
        with requests.get(candidate.download_url, headers=headers, stream=True, timeout=25) as r:
            r.raise_for_status()
            total = 0
            limit_bytes = max_download_mb * 1024 * 1024
            with open(raw_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        total += len(chunk)
                        if total > limit_bytes:
                            break
                        fh.write(chunk)
        if candidate.tier == "tier_2_photo_ken_burns":
            try:
                im = Image.open(raw_path).convert("RGB")
                if im.width < 480 or im.height < 480:
                    raw_path.unlink(missing_ok=True)
                    return None
                return {"width": im.width, "height": im.height, "duration": 8.0, "is_image": True}
            except Exception:
                raw_path.unlink(missing_ok=True)
                return None

        info = probe_video(raw_path)
        if not info or info["duration"] < 2.5 or info["width"] < 320:
            raw_path.unlink(missing_ok=True)
            return None
        return info
    except Exception:
        raw_path.unlink(missing_ok=True)
        return None


def _transcode_to_mp4(raw_path: Path, info: dict, dest_mp4: Path, trim_ends_sec: float = 0.0) -> Optional[dict]:
    """Transcode approved raw clip into clean H.264 MP4 (trims 2.0s start & end if Wikimedia)."""
    try:
        dur = float(info.get("duration") or 10.0)
        if trim_ends_sec > 0.0 and dur > (trim_ends_sec * 2 + 2.0):
            start_at = trim_ends_sec
            slice_dur = min(22.0, dur - trim_ends_sec * 2.0)
        else:
            start_at = 1.5 if dur > 18.0 else 0.0
            slice_dur = 22.0
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{start_at:.2f}",
                "-i", str(raw_path),
                "-t", f"{slice_dur:.2f}",
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(dest_mp4),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return probe_video(dest_mp4)
    except Exception:
        return None
    finally:
        raw_path.unlink(missing_ok=True)


def _next_free_path(assets_dir: Path, scene_id: str) -> Path:
    base = assets_dir / f"{scene_id}.mp4"
    if not base.exists():
        return base
    k = 2
    while (assets_dir / f"{scene_id}_alt{k}.mp4").exists():
        k += 1
    return assets_dir / f"{scene_id}_alt{k}.mp4"


def resolve_scene_sfx_cues(
    scene_idx: int,
    total_scenes: int,
    scene: SceneSpec,
    memory: StudioMemory,
    force_transition: bool = True,
    force_animation: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    raw_cue = str(getattr(scene, "sfx_cue", "") or "").strip().lower()
    if raw_cue in ("whoosh", "pop", "riser", "chime", "ding", "spark"):
        trans_cue: Optional[str] = raw_cue
    elif force_transition:
        if scene_idx == 0:
            trans_cue = "riser"
        elif total_scenes >= 3 and scene_idx == total_scenes - 1:
            trans_cue = "chime"
        elif total_scenes >= 4 and scene_idx == total_scenes - 2:
            trans_cue = "ding"
        else:
            trans_cue = "whoosh" if scene_idx % 2 == 1 else "pop"
    else:
        trans_cue = None

    anim_cue: Optional[str] = None
    has_visual_anim = bool(getattr(scene, "mechanism", None) is not None or getattr(scene, "overlay_badge", None))
    if force_animation and has_visual_anim:
        anim_cue = "pop" if trans_cue == "spark" else ("spark" if trans_cue == "pop" or scene_idx % 2 == 1 else "pop")

    return (
        memory.get_sfx_path(trans_cue) if trans_cue else None,
        memory.get_sfx_path(anim_cue) if anim_cue else None,
    )


_FILLER_AND_ADJECTIVES: frozenset[str] = frozenset({
    "the", "and", "with", "from", "into", "that", "this", "for", "are", "was", "in", "on", "of", "to",
    "a", "an", "at", "by", "or", "as", "is", "be", "has", "had", "can", "not", "but", "out", "off",
    "all", "its", "one", "two", "big", "low", "high", "use", "used", "using", "show", "showing",
    "video", "clip", "clips", "footage", "close", "closeup", "macro", "shot", "view", "real",
    "screen", "monitor", "computer", "dark", "light", "bright", "cold", "hot", "warm", "tiny",
    "small", "large", "fast", "slow", "hard", "soft", "background", "modern", "clean", "simple",
})

_DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cooktop": ("stove", "induction", "kitchen", "cooking", "pan", "pot", "burner"),
    "stove": ("cooktop", "kitchen", "cooking", "pan", "burner"),
    "induction": ("cooktop", "stove", "coil", "pan", "magnetic", "cooking"),
    "smartphone": ("phone", "mobile", "iphone", "android", "screen", "touchscreen"),
    "phone": ("smartphone", "mobile", "iphone", "display", "screen", "charger"),
    "checkout": ("payment", "terminal", "card", "credit", "nfc", "register"),
    "nfc": ("contactless", "payment", "card", "phone", "chip", "coil"),
    "oled": ("display", "screen", "smartphone", "pixel", "led", "tv"),
    "earbuds": ("earphones", "headphones", "audio", "wireless"),
    "microwave": ("oven", "kitchen", "food", "cooking", "heating"),
    "fridge": ("refrigerator", "freezer", "cooling", "ice", "kitchen"),
    "teflon": ("pan", "nonstick", "skillet", "cooking", "coating", "egg"),
    "smartwatch": ("watch", "fitness", "wrist", "wearable", "tracker", "heart"),
    "airplane": ("aircraft", "plane", "jet", "wing", "flight", "window"),
    "battery": ("charging", "charger", "power", "energy", "electric", "lithium"),
    "solar": ("panel", "photovoltaic", "sunlight", "renewable", "energy"),
    "filter": ("water", "purifier", "clean", "charcoal", "glass"),
}

_IRRELEVANT_STOCK_NOISE: frozenset[str] = frozenset({
    "jellyfish", "cow", "cows", "penguin", "penguins", "butterfly", "butterflies", "snake", "python",
    "squirrel", "kitten", "puppy", "cat", "dog", "moose", "alpaca", "sheep", "goat", "horse", "flamingo",
    "deer", "duck", "bird", "fireworks", "skateboard", "skater", "wedding", "bride", "tulip", "tulips",
    "poppy", "snowdrop", "daisy", "lavender", "bacon", "beer", "cocktail", "pilates", "yoga", "ballet",
    "marbles", "antiquariat", "amethyst", "swimming pool", "parking garage",
})


def _extract_scene_topic_terms(scene: SceneSpec) -> set[str]:
    raw_blob = " ".join([scene.visual_query, *scene.fallback_queries]).lower()
    base_tokens = [t for t in re.findall(r"[a-z]{3,}", raw_blob) if t not in _FILLER_AND_ADJECTIVES]
    expanded: set[str] = set(base_tokens)
    for t in base_tokens:
        expanded.update(_DOMAIN_SYNONYMS.get(t, ()))
    return expanded


def _metadata_relevance_score(cand: VideoCandidate, scene: SceneSpec, topic_terms: set[str]) -> int:
    title_low = cand.title.lower()
    if not _is_clean_real_title(title_low, getattr(scene, "negative_keywords", [])):
        return -10
    meta_clean = re.sub(r"^(?:pixabay(?:\s+photo)?|pexels|coverr|unsplash|openverse|nasa)\s*#?\d*\s*-\s*", "", title_low)
    meta_tokens = set(re.findall(r"[a-z]{3,}", meta_clean))
    scene_raw_low = " ".join([scene.visual_query, *scene.fallback_queries, scene.narration or ""]).lower()
    for noise_tok in _IRRELEVANT_STOCK_NOISE:
        if noise_tok in meta_tokens and noise_tok not in scene_raw_low:
            return -10
    if not topic_terms:
        return 1
    meta_stems = meta_tokens | {t.rstrip("s") for t in meta_tokens if len(t) > 4}
    topic_stems = topic_terms | {t.rstrip("s") for t in topic_terms if len(t) > 4}
    return len(meta_stems & topic_stems)


def _should_prioritize_nasa(scene: SceneSpec) -> bool:
    blob = f"{scene.visual_query} {scene.narration}".lower()
    return bool(re.search(r"\b(?:space|orbit|rocket|satellite|solar|optics|laser|earth|atmosphere|physics|thermal|infrared|radar)\b", blob))


def _gather_tier1_video_candidates(scene: SceneSpec, query: str) -> list[VideoCandidate]:
    """Gather Tier 1 real video candidates without Pexels requirement."""
    cands: list[VideoCandidate] = []
    if _should_prioritize_nasa(scene):
        cands.extend(search_nasa_videos(query, limit=4))
    cands.extend(search_pixabay(query, per_page=6))
    cands.extend(search_coverr(query, limit=5))
    cands.extend(search_pexels(query, per_page=4))  # returns [] immediately if unset or 401/403/429
    if not _should_prioritize_nasa(scene) and len(cands) < 2:
        cands.extend(search_nasa_videos(query, limit=3))
    if len(cands) < 2:
        cands.extend(search_wikimedia_videos(query, limit=5))
    return cands


def _gather_tier2_photo_candidates(query: str) -> list[VideoCandidate]:
    cands: list[VideoCandidate] = []
    cands.extend(search_unsplash_portrait(query, per_page=5))
    cands.extend(search_pixabay_photo(query, per_page=5))
    if len(cands) < 3:
        cands.extend(search_openverse_photo(query, limit=4))
    return cands


# === Quota-Safe Contact-Sheet Vision QA (1 Single API Call per Episode) ===

def _extract_midframe_for_sheet(media_path: Path, is_image: bool, out_jpg: Path) -> Optional[Path]:
    if is_image and media_path.exists():
        try:
            im = Image.open(media_path).convert("RGB")
            im.thumbnail((420, 420))
            im.save(out_jpg, quality=85)
            return out_jpg
        except Exception:
            return None
    info = probe_video(media_path)
    mid = max(0.3, (float(info["duration"]) * 0.45) if info else 1.5)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{mid:.2f}", "-i", str(media_path), "-frames:v", "1", "-vf", "scale=420:-2", "-q:v", "4", str(out_jpg)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return out_jpg if out_jpg.exists() else None


def _build_batch_contact_sheet(tiles: list[tuple[str, str, Path]], out_jpg: Path) -> Optional[Path]:
    loaded: list[tuple[str, str, Image.Image]] = []
    for key, query, p in tiles:
        if p.exists():
            im = Image.open(p).convert("RGB")
            im.thumbnail((360, 360))
            loaded.append((key, query, im))
    if not loaded:
        return None
    cols = min(3, len(loaded))
    rows = (len(loaded) + cols - 1) // cols
    cell_w, cell_h, hdr = 380, 320, 38
    sheet = Image.new("RGB", (cols * cell_w + 20, rows * (cell_h + hdr) + 20), (16, 20, 30))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, (key, q, im) in enumerate(loaded):
        r, c = divmod(i, cols)
        x = 10 + c * cell_w
        y = 10 + r * (cell_h + hdr)
        draw.text((x + 6, y + 6), f"{key.upper()}: {q[:34]}", fill=(255, 224, 92), font=font)
        # Center image cleanly inside cell
        scale = min((cell_w - 16) / im.width, (cell_h - 10) / im.height)
        rw, rh = max(40, int(im.width * scale)), max(40, int(im.height * scale))
        resized = im.resize((rw, rh), Image.Resampling.LANCZOS)
        sheet.paste(resized, (x + (cell_w - rw) // 2, y + hdr + (cell_h - rh) // 2))
    sheet.save(out_jpg, quality=88)
    return out_jpg


class _ShotVerdict(BaseModel):
    shot_id: str = Field(description="Exact tile label e.g. 'shot_1', 'shot_2'")
    family_safe: bool
    has_watermark_or_text_slide: bool = Field(description="True if logo, text banner, presentation slide, university bumper, or stock watermark visible")
    is_off_topic_scene: bool = Field(description="True if wildly unrelated (e.g. empty parking garage, swimming pool, palm trees, fireworks) vs scene_narration/visual_query")
    relevance_score: float = Field(ge=0, le=10)
    reason: str = ""


class _BatchContactSheetScreen(BaseModel):
    verdicts: list[_ShotVerdict]


def _batch_vision_screen_sheet(
    sheet_jpg: Path,
    shot_payloads: list[dict[str, Any]],
) -> dict[str, _ShotVerdict]:
    """Screen all candidate shots in ONE single Vision LLM call (respecting 429 backoff)."""
    if os.environ.get("ASSET_VISION_SCREEN", "true").lower() != "true":
        return {}
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        return {}
    try:
        from src.providers import call_structured_agent

        res = call_structured_agent(
            provider="auto",
            model=os.environ.get("GEMINI_SCREEN_MODEL", "gemini-3.5-flash-lite"),
            system_prompt=(
                f"You are a calibrated Batch Visual QA Screener for '{get_brand_name()}', an Everyday Sci-Tech Shorts studio. "
                "Inspect all numbered tiles (`shot_1`, `shot_2`, ...) in the attached contact sheet grid in ONE pass.\n"
                "Rules:\n"
                "- Set `has_watermark_or_text_slide: true` if foreign text slides, lecture screens, university bumpers, or big watermarks appear.\n"
                "- Set `is_off_topic_scene: true` if the visual is blatantly off-topic (e.g. empty parking garage, swimming pool, palm trees for hardware/cells).\n"
                "- Score `relevance_score` 0-10 honestly: 8-10 for crisp direct subject match, 6-7.4 for plausible real-world context/hardware/lab footage."
            ),
            user_payload={"shots": shot_payloads},
            response_model=_BatchContactSheetScreen,
            handoff_path=None,
            image_paths=[sheet_jpg],
        )
        return {v.shot_id.strip().lower(): v for v in res.verdicts}
    except Exception as exc:
        print(f"[asset_scout] Batch Contact-Sheet Vision QA skipped safely ({exc}); using metadata relevance.")
        return {}


def scout_assets_for_scenes(
    scenes: list[SceneSpec],
    assets_dir: Path,
    memory: StudioMemory,
    manual_overrides: Optional[dict[str, VideoCandidate]] = None,
    exclude_urls: Optional[set[str]] = None,
    existing_assets: Optional[dict[str, SceneAsset]] = None,
    target_scene_ids: Optional[set[str]] = None,
    tried_urls_out: Optional[set[str]] = None,
) -> Agent6Output:
    """Scout visuals via 3-Tier Waterfall + 1-Call Contact-Sheet Vision QA (zero hard crashes)."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    used_urls: set[str] = set(exclude_urls or ())
    used_stems: set[str] = set()
    try:
        from src.tools.telegram_notify import get_owner_runtime_overrides

        _ro = get_owner_runtime_overrides()
    except Exception:
        _ro = {}
    force_trans_sfx = bool(_ro.get("force_sfx_every_transition", True))
    force_anim_sfx = bool(_ro.get("force_sfx_every_animation", True))

    total_scenes = len(scenes)
    selected: dict[int, tuple[VideoCandidate, Path, dict, bool]] = {}
    unchanged_assets: dict[str, SceneAsset] = {}

    # Pass 1: Gather best Tier 1 video (or fallback Tier 2 photo if Tier 1 empty) per target scene
    tmp_dir = assets_dir / "_cand_stage"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for idx, scene in enumerate(scenes):
        if target_scene_ids is not None and scene.scene_id not in target_scene_ids and existing_assets and scene.scene_id in existing_assets:
            unchanged_assets[scene.scene_id] = existing_assets[scene.scene_id]
            used_urls.add(existing_assets[scene.scene_id].source_url)
            continue

        if manual_overrides and scene.scene_id in manual_overrides:
            cand = manual_overrides[scene.scene_id]
            dest = _next_free_path(assets_dir, scene.scene_id)
            raw_p = tmp_dir / f"{scene.scene_id}_manual.raw"
            info = _download_raw_clip(cand, raw_p)
            if info:
                selected[idx] = (cand, raw_p, info, False)
                continue

        topic_terms = _extract_scene_topic_terms(scene)
        queries = [scene.visual_query, *list(scene.fallback_queries)]
        # Broaden with crisp 2-word anchor query
        words = [w for w in re.findall(r"[A-Za-z]+", scene.visual_query.lower()) if len(w) >= 3 and w not in _FILLER_AND_ADJECTIVES]
        if len(words) >= 2:
            queries.append(" ".join(words[:2]))

        tier1_pool: list[tuple[int, VideoCandidate]] = []
        for q in queries[:3]:
            for cand in _gather_tier1_video_candidates(scene, q):
                if cand.download_url in used_urls or cand.page_url in used_urls:
                    continue
                ms = _metadata_relevance_score(cand, scene, topic_terms)
                if ms < 0:
                    continue
                tier1_pool.append((ms, cand))
            if len([c for m, c in tier1_pool if m >= 1]) >= 2:
                break

        source_rank = {"coverr": 0, "pixabay": 1, "nasa": 2, "pexels": 3, "wikimedia_commons": 4}
        tier1_pool.sort(
            key=lambda pr: (
                -pr[0],
                0 if pr[1].height > pr[1].width else 1,  # prioritize portrait assets!
                source_rank.get(pr[1].source, 9),
                -min(pr[1].width, 1920),
            )
        )

        picked = None
        for ms, cand in tier1_pool[:4]:
            stem = cand.title.lower()[:32]
            if stem in used_stems or cand.download_url in used_urls:
                continue
            raw_p = tmp_dir / f"{scene.scene_id}_t1.raw"
            used_urls.add(cand.download_url)
            used_urls.add(cand.page_url)
            info = _download_raw_clip(cand, raw_p)
            if info:
                used_stems.add(stem)
                picked = (cand, raw_p, info, False)
                break

        if picked is None:
            # Escalate immediately to Tier 2 (Portrait Photo + Ken Burns)
            tier2_pool: list[tuple[int, VideoCandidate]] = []
            for q in queries[:2]:
                for cand in _gather_tier2_photo_candidates(q):
                    if cand.download_url in used_urls:
                        continue
                    ms = _metadata_relevance_score(cand, scene, topic_terms)
                    if ms >= 0:
                        tier2_pool.append((ms, cand))
            tier2_pool.sort(key=lambda pr: (-pr[0], 0 if pr[1].source == "unsplash" else 1))
            for ms, cand in tier2_pool[:3]:
                raw_p = tmp_dir / f"{scene.scene_id}_t2.jpg"
                used_urls.add(cand.download_url)
                info = _download_raw_clip(cand, raw_p)
                if info:
                    if cand.source == "unsplash":
                        _trigger_unsplash_download(cand)
                    picked = (cand, raw_p, info, True)
                    break

        if picked is not None:
            selected[idx] = picked

    # Pass 2: Build 1 single Contact-Sheet grid for ALL candidate shots & validate in ONE Vision API call!
    tiles: list[tuple[str, str, Path]] = []
    payloads: list[dict[str, Any]] = []
    idx_to_shot_id: dict[int, str] = {}

    for idx in sorted(selected.keys()):
        cand, raw_p, info, is_img = selected[idx]
        shot_id = f"shot_{idx + 1}"
        idx_to_shot_id[idx] = shot_id
        tile_jpg = tmp_dir / f"{shot_id}_tile.jpg"
        if _extract_midframe_for_sheet(raw_p, is_img, tile_jpg):
            tiles.append((shot_id, scenes[idx].visual_query, tile_jpg))
            payloads.append({
                "shot_id": shot_id,
                "is_hook_shot": idx == 0,
                "visual_query": scenes[idx].visual_query,
                "scene_narration": scenes[idx].narration[:140],
                "candidate_title": cand.title[:70],
            })

    verdicts: dict[str, _ShotVerdict] = {}
    if tiles:
        sheet_jpg = tmp_dir / "batch_contact_sheet.jpg"
        if _build_batch_contact_sheet(tiles, sheet_jpg):
            verdicts = _batch_vision_screen_sheet(sheet_jpg, payloads)
            print(f"[asset_scout] Batch Contact-Sheet Vision QA checked {len(tiles)} shot(s) in 1 API call.")

    # Pass 3: Enforce Shot #1 (>= 7.5) & Shot #2..N (>= 6.0); seamlessly escalate failed shots Tier 1 -> Tier 2 -> Tier 3
    results: list[SceneAsset] = []
    for idx, scene in enumerate(scenes):
        trans_sfx_path, anim_sfx_path = resolve_scene_sfx_cues(
            scene_idx=idx, total_scenes=total_scenes, scene=scene, memory=memory,
            force_transition=force_trans_sfx, force_animation=force_anim_sfx,
        )
        if scene.scene_id in unchanged_assets:
            results.append(unchanged_assets[scene.scene_id])
            continue

        dest_mp4 = _next_free_path(assets_dir, scene.scene_id)
        min_req = 7.5 if idx == 0 else 6.0
        needs_escalation = idx not in selected

        if idx in selected:
            cand, raw_p, info, is_img = selected[idx]
            sid = idx_to_shot_id.get(idx, f"shot_{idx + 1}")
            v = verdicts.get(sid)
            if v is not None:
                passed = (
                    v.family_safe
                    and not v.has_watermark_or_text_slide
                    and not v.is_off_topic_scene
                    and v.relevance_score >= min_req
                )
                print(
                    f"[asset_scout] {scene.scene_id} ({sid}, min={min_req}): rel={v.relevance_score} "
                    f"safe={v.family_safe} wm={v.has_watermark_or_text_slide} off={v.is_off_topic_scene} -> {'PASS' if passed else 'ESCALATE'}"
                )
                if not passed:
                    raw_p.unlink(missing_ok=True)
                    needs_escalation = True

        final_cand: Optional[VideoCandidate] = None
        final_info: Optional[dict] = None

        if not needs_escalation and idx in selected:
            cand, raw_p, info, is_img = selected[idx]
            if is_img:
                final_info = _render_image_to_ken_burns_mp4(raw_p, dest_mp4, duration_sec=8.0, motion=scene.motion)
                raw_p.unlink(missing_ok=True)
            else:
                final_info = _transcode_to_mp4(raw_p, info, dest_mp4, trim_ends_sec=2.0 if cand.needs_trim_ends else 0.0)
            if final_info:
                final_cand = cand

        # Escalate failed Tier 1 -> Tier 2 Portrait Photo + Ken Burns
        if final_cand is None:
            topic_terms = _extract_scene_topic_terms(scene)
            fb_q = (scene.fallback_queries[0] if scene.fallback_queries else scene.visual_query)
            if idx in selected and not selected[idx][3]:  # previously tried video, now try portrait photo
                t2_list = _gather_tier2_photo_candidates(scene.visual_query) or _gather_tier2_photo_candidates(fb_q)
                t2_list = [c for c in t2_list if _metadata_relevance_score(c, scene, topic_terms) >= 0 and c.download_url not in used_urls]
                for t2c in t2_list[:2]:
                    img_p = tmp_dir / f"{scene.scene_id}_esc_t2.jpg"
                    used_urls.add(t2c.download_url)
                    if _download_raw_clip(t2c, img_p):
                        if t2c.source == "unsplash":
                            _trigger_unsplash_download(t2c)
                        k_info = _render_image_to_ken_burns_mp4(img_p, dest_mp4, duration_sec=8.0, motion=scene.motion)
                        img_p.unlink(missing_ok=True)
                        if k_info:
                            final_cand, final_info = t2c, k_info
                            print(f"[asset_scout] {scene.scene_id}: escalated cleanly to Tier 2 Photo + Ken Burns ({t2c.source})")
                            break

        # Final Escalation -> Tier 3 AI-Generated 9:16 Scene + Ken Burns (Zero Hard-Crash Policy!)
        if final_cand is None:
            ai_img = tmp_dir / f"{scene.scene_id}_tier3_ai.jpg"
            gen_model = _generate_ai_scene_image(scene, ai_img)
            if not gen_model or not ai_img.exists():
                _draw_zero_crash_optical_backdrop(ai_img)
                gen_model = "optical_studio_backdrop"
            k_info = _render_image_to_ken_burns_mp4(ai_img, dest_mp4, duration_sec=8.0, motion=scene.motion)
            ai_img.unlink(missing_ok=True)
            if not k_info:
                raise RuntimeError(f"FFmpeg Ken Burns failed unexpectedly for {scene.scene_id}")
            final_cand = VideoCandidate(
                source=gen_model,
                title=f"AI 9:16 Scene ({gen_model}) - {scene.visual_query}"[:120],
                download_url=f"ai://{gen_model}/{scene.scene_id}",
                page_url=f"ai://{gen_model}/{scene.scene_id}",
                license="AI-Generated Studio Visual (Zero Copyright)",
                duration_sec=float(k_info.get("duration") or 8.0),
                width=1080,
                height=1920,
                tier="tier_3_ai_scene_ken_burns",
            )
            final_info = k_info
            print(f"[asset_scout] {scene.scene_id}: resolved cleanly via Tier 3 ({gen_model}) + Ken Burns")

        assert final_cand is not None and final_info is not None
        results.append(
            SceneAsset(
                scene_id=scene.scene_id,
                footage_path=str(dest_mp4),
                source=final_cand.source,
                source_url=final_cand.page_url,
                license=final_cand.license,
                duration_sec=round(float(final_info["duration"]), 2),
                width=int(final_info["width"]),
                height=int(final_info["height"]),
                footage_tier=final_cand.tier,
                sfx_path=trans_sfx_path,
                animation_sfx_path=anim_sfx_path,
                notes=final_cand.title,
            )
        )

    # Cleanup temp staging dir
    try:
        for leftover in tmp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        tmp_dir.rmdir()
    except Exception:
        pass

    if tried_urls_out is not None:
        tried_urls_out.update(used_urls)
    return Agent6Output(assets=results, bgm_path=memory.get_bgm_path())
