"""Agent 6 Tool: Real Science Video Footage & SFX Scout.

Sources real-world video clips (strictly avoiding animations/cartoons/lectures):
1. Pexels Video API (automatically enabled if PEXELS_API_KEY env var is set)
2. Pixabay Video API (automatically enabled if PIXABAY_API_KEY env var is set; free & active)
3. Wikimedia Commons API (free, no API key needed; CC0/CC-BY real science & kitchen videos)
4. NASA Images & Video API (free, no API key needed; Public Domain physics/earth/science HD clips)

Reuses local SFX cache via StudioMemory so basic SFX are never re-fetched.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
import urllib3.util.connection as _urllib3_conn

from src.brand_config import get_brand_name, get_gdrive_root
from src.memory import StudioMemory
from src.state import Agent6Output, SceneAsset, SceneSpec

# Prefer IPv4 to avoid transient IPv6 routing failures on container hosts
_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

USER_AGENT = f"{get_brand_name()}Studio/1.0 (everyday-scitech-bot)"

DISALLOWED_TITLE_TOKENS = (
    "animation",
    "animated",
    "cartoon",
    "3d",
    "blender",
    "simulation",
    "simulated",
    "diagram",
    "lecture",
    "presentation",
    "interview",
    "talk",
    "podcast",
    "webinar",
    "slideshow",
    "trailer",
    "logo",
    "intro",
    "music video",
    "album",
    "karaoke",
    "gameplay",
    "screen recording",
    "screencast",
    "wikipedia",
    "wp25",
    "wikimedia",
    "wikimania",
    "anthem",
    "caption",
    "billboard",
    "commercial",
    "promo",
    "donation",
    "scp-",
    "tutorial",
    "ukulele",
    "guitar",
    "horseback",
    "flamingo",
    "cctv",
    "wallet theft",
    "brush pack",
    "painter",
    "hubblecast",
    "esocast",
    "sora in action",
    "graduation",
    "from farm to plate",
    "recipe",
    "street light",
    "full moon",
    "carpentry",
    "woodworking",
    "screw pump",
    "grouted connection",
    "primordial germ cells",
    "astronaut moments",
    "nogizaka",
)

# Family-safety blocklist (checked against title + categories + description metadata)
UNSAFE_TOKENS = (
    "nude", "nudity", "naked", "topless", "nsfw", "porn", "erotic", "sex", "sexual",
    "genital", "penis", "vagina", "breast", "nipple", "masturbat", "orgasm", "fetish",
    "bdsm", "bondage", "lingerie", "bikini", "stripper", "striptease", "intercourse",
    "condom", "explicit", "adult film", "xxx", "gore", "corpse", "dead body", "autopsy",
    "beheading", "execution", "suicide", "murder", "shoot", "shooting", "knife-wielding",
    "suspect", "gun", "weapon", "war crime",
    "drug use", "injecting", "surgery", "pregnan", "childbirth",
    "dancing", "pole dance", "fashion show", "model posing",
)


def _is_family_safe_text(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    blob = re.sub(r"<[^>]+>", " ", blob)
    return not any(tok in blob for tok in UNSAFE_TOKENS)


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
    thumb_url: Optional[str] = None


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


def _is_real_video_title(title: str) -> bool:
    low = title.lower()
    return not any(tok in low for tok in DISALLOWED_TITLE_TOKENS)


def search_pexels(query: str, per_page: int = 8, orientation: Optional[str] = "portrait") -> list[VideoCandidate]:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page}
    if orientation:
        params["orientation"] = orientation
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": api_key, "User-Agent": USER_AGENT},
        params=params,
        timeout=20,
    )
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
                thumb_url=v.get("image"),
            )
        )
    return out


def search_pixabay(query: str, per_page: int = 8) -> list[VideoCandidate]:
    """Search Pixabay Video API when PIXABAY_API_KEY is configured."""
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return []
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
            timeout=20,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    out: list[VideoCandidate] = []
    for v in resp.json().get("hits", []):
        dur = float(v.get("duration") or 0)
        if dur < 3.0:
            continue
        tags = str(v.get("tags") or "")
        if not _is_real_video_title(tags) or not _is_family_safe_text(tags):
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
                thumb_url=None,
            )
        )
    return out


def search_nasa_videos(query: str, limit: int = 5) -> list[VideoCandidate]:
    """Search NASA Images & Video API (0 API key, Public Domain) for physics/earth/science HD clips."""
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            headers={"User-Agent": USER_AGENT},
            params={"q": query, "media_type": "video"},
            timeout=20,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    items = resp.json().get("collection", {}).get("items", [])
    out: list[VideoCandidate] = []
    for item in items[: limit * 2]:
        if len(out) >= limit:
            break
        data_list = item.get("data") or []
        if not data_list:
            continue
        data = data_list[0]
        title = str(data.get("title") or "").strip()
        desc = str(data.get("description") or "")
        nasa_id = str(data.get("nasa_id") or "").strip()
        if not title or not _is_real_video_title(title) or not _is_family_safe_text(title, desc):
            continue
        collection_url = item.get("href")
        if not collection_url:
            continue
        try:
            col_resp = requests.get(collection_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if col_resp.status_code != 200:
            continue
        try:
            asset_urls = col_resp.json()
        except ValueError:
            continue
        mp4_urls = [u for u in asset_urls if isinstance(u, str) and u.lower().endswith(".mp4")]
        if not mp4_urls:
            continue
        mp4_url = None
        for suffix in ("~medium.mp4", "~small.mp4", "~orig.mp4", "~mobile.mp4"):
            matched = [u for u in mp4_urls if u.lower().endswith(suffix)]
            if matched:
                mp4_url = matched[0]
                break
        if mp4_url is None:
            mp4_url = mp4_urls[0]
        thumb_url = None
        for lnk in item.get("links") or []:
            if isinstance(lnk, dict) and lnk.get("rel") == "preview" and lnk.get("href"):
                thumb_url = lnk["href"]
                break
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
                thumb_url=thumb_url,
            )
        )
    return out


_FLOW_BILINGUAL_SYNONYMS: dict[str, set[str]] = {
    "nanas": {"pineapple", "bromelain", "enzim", "enzyme"},
    "pineapple": {"nanas", "bromelain", "enzyme"},
    "daging": {"meat", "beef", "steak", "protein", "serat"},
    "meat": {"daging", "beef", "steak"},
    "beef": {"daging", "steak", "meat"},
    "kopi": {"coffee", "caffeine", "kafein", "seduh", "brew"},
    "coffee": {"kopi", "caffeine", "brew"},
    "air": {"water", "droplet", "h2o", "tetes", "liquid", "cairan"},
    "water": {"air", "droplet", "tetes", "liquid"},
    "minyak": {"oil", "frying", "goreng", "lipid", "lemak"},
    "oil": {"minyak", "frying", "goreng"},
    "es": {"ice", "beku", "frost", "kristal", "crystal"},
    "ice": {"es", "beku", "frost", "melting", "mencair"},
    "garam": {"salt", "nacl", "sodium", "kristal"},
    "salt": {"garam", "nacl", "sodium", "crystal"},
    "gula": {"sugar", "karamel", "caramel", "glukosa"},
    "sugar": {"gula", "caramel", "glucose"},
    "api": {"fire", "flame", "pembakaran", "nyala", "burn"},
    "fire": {"api", "flame", "burn", "combustion"},
    "karat": {"rust", "korosi", "corrosion", "iron", "besi", "oksidasi"},
    "rust": {"karat", "korosi", "corrosion", "iron", "besi"},
    "sel": {"cell", "cells", "mikroskop", "microscope", "bakteri"},
    "cell": {"sel", "microscope", "mikroskop"},
    "telur": {"egg", "albumin", "protein"},
    "egg": {"telur", "protein", "boiling"},
    "bawang": {"onion", "garlic", "tear", "mata"},
    "onion": {"bawang", "garlic"},
    "susu": {"milk", "casein", "kasein", "emulsion"},
    "milk": {"susu", "emulsion"},
    "pisang": {"banana", "buah", "fruit"},
    "banana": {"pisang", "fruit"},
    "asam": {"acid", "ph", "cuka", "vinegar", "lemon"},
    "acid": {"asam", "vinegar", "lemon"},
    "panas": {"heat", "hot", "boiling", "mendidih", "thermal"},
    "mendidih": {"boiling", "bubbles", "gelembung", "heat"},
    "boiling": {"mendidih", "bubbles", "panas"},
    "goreng": {"frying", "oil", "minyak", "pan", "wajan"},
    "frying": {"goreng", "oil", "minyak", "pan"},
    "kristal": {"crystal", "crystals", "lattice", "kisi"},
    "crystal": {"kristal", "salt", "garam"},
}


def list_flow_clip_bank() -> list[dict]:
    """Fetch available Google AI Flow MP4 clips from <gdrive_root>/Flow_Clip_Bank via Apps Script webhook."""
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
            timeout=25,
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


def _score_flow_clip_for_scene(filename: str, scene: SceneSpec) -> int:
    stem = Path(filename).stem.lower()
    raw_tokens = [
        t for t in re.findall(r"[a-z0-9]+", stem)
        if len(t) >= 2 and t not in {"flow", "clip", "clips", "video", "scene", "9x16", "hd", "mp4", "mov"}
    ]
    if not raw_tokens:
        return 0
    expanded_file_tokens: set[str] = set(raw_tokens)
    for tok in raw_tokens:
        expanded_file_tokens |= _FLOW_BILINGUAL_SYNONYMS.get(tok, set())

    scene_blob = " ".join([scene.visual_query, *scene.fallback_queries, scene.narration or ""]).lower()
    scene_tokens = set(re.findall(r"[a-z0-9]+", scene_blob))
    overlap = expanded_file_tokens & scene_tokens
    return len(overlap)


def _try_match_flow_bank_clip(
    scene: SceneSpec,
    flow_clips: list[dict],
    used_flow_ids: set[str],
    used_urls: set[str],
    dest_mp4: Path,
    episode_id: str,
    memory: StudioMemory,
) -> Optional[tuple[VideoCandidate, dict]]:
    if not flow_clips:
        return None
    scored: list[tuple[int, dict]] = []
    for clip in flow_clips:
        fid = str(clip.get("file_id") or "")
        wurl = str(clip.get("url") or clip.get("web_url") or f"flow://{fid}")
        if not fid or fid in used_flow_ids or wurl in used_urls:
            continue
        sc = _score_flow_clip_for_scene(str(clip.get("filename") or ""), scene)
        if sc >= 1:
            scored.append((sc, clip))
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("filename") or "")))

    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        return None
    from src.tools.gdrive_sync import _normalize_webhook_url

    webhook_url = _normalize_webhook_url(raw_url)
    token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()

    for _sc, clip in scored[:3]:
        fid = str(clip["file_id"])
        fname = str(clip["filename"])
        wurl = str(clip.get("url") or clip.get("web_url") or f"flow://{fid}")
        try:
            dl_resp = requests.post(
                webhook_url,
                json={"action": "download_file", "token": token, "file_id": fid},
                timeout=90,
            )
            if dl_resp.status_code != 200:
                continue
            dl_json = dl_resp.json()
            if not dl_json.get("ok") or not dl_json.get("content_base64"):
                continue
            raw_path = dest_mp4.with_suffix(".raw_dl")
            raw_path.write_bytes(base64.b64decode(dl_json["content_base64"]))
            raw_info = probe_video(raw_path)
            if not raw_info or raw_info.get("duration", 0) < 2.0:
                raw_path.unlink(missing_ok=True)
                continue
            ok, _clean, reason = vision_screen_clip(raw_path, scene)
            print(f"[asset_scout] {scene.scene_id} Flow_Clip_Bank '{fname}': ok={ok} ({reason[:140]})")
            used_urls.add(wurl)
            if not ok:
                raw_path.unlink(missing_ok=True)
                continue
            chosen_info = _transcode_to_mp4(raw_path, raw_info, dest_mp4)
            if not chosen_info:
                raw_path.unlink(missing_ok=True)
                continue
            used_flow_ids.add(fid)
            try:
                memory.mark_flow_clip_used(fid, fname, episode_id)
            except Exception:
                pass
            cand = VideoCandidate(
                source="google_flow",
                title=f"Google Flow - {fname}"[:120],
                download_url=wurl,
                page_url=wurl,
                license="Google AI Flow (User Subscription Bank)",
                duration_sec=float(chosen_info.get("duration") or raw_info.get("duration") or 6.0),
                width=int(chosen_info.get("width") or 1080),
                height=int(chosen_info.get("height") or 1920),
                thumb_url=None,
            )
            return cand, chosen_info
        except Exception as exc:
            print(f"[asset_scout] Peringatan ambil Flow_Clip_Bank '{fname}': {exc}")
    return None


def search_wikimedia_videos(query: str, limit: int = 12) -> list[VideoCandidate]:
    """Search Wikimedia Commons for real video files (.webm / .mp4 / .ogv)."""
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
        "viurlwidth": "640",
    }
    resp = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        headers=headers,
        params=params,
        timeout=25,
    )
    if resp.status_code != 200:
        return []
    pages = resp.json().get("query", {}).get("pages", {})
    out: list[VideoCandidate] = []
    for page in pages.values():
        title = page.get("title", "")
        if not _is_real_video_title(title):
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
        if dur < 3.0 or dur > 600.0 or w < 320 or h < 240:
            continue
        url = vi.get("url")
        if not url:
            continue
        meta = vi.get("extmetadata", {})
        meta_text = " ".join(
            str(meta.get(k, {}).get("value", ""))
            for k in ("Categories", "ImageDescription", "ObjectName", "Restrictions")
        )
        if not _is_family_safe_text(title, meta_text):
            continue
        lic = meta.get("LicenseShortName", {}).get("value", "Wikimedia Commons")
        lic = re.sub(r"<[^>]+>", "", str(lic)).strip()
        out.append(
            VideoCandidate(
                source="wikimedia_commons",
                title=title.replace("File:", ""),
                download_url=url,
                page_url=vi.get("descriptionurl", url),
                license=lic,
                duration_sec=dur,
                width=w,
                height=h,
                thumb_url=vi.get("thumburl"),
            )
        )
    # Prefer HD, shorter focused clips (5s - 90s)
    out.sort(key=lambda c: (0 if 5 <= c.duration_sec <= 90 else 1, -min(c.width, 1920)))
    return out


def _download_raw_clip(
    candidate: VideoCandidate,
    raw_path: Path,
    max_download_mb: int = 32,
) -> Optional[dict]:
    """Download candidate video up to max_download_mb and verify it is decodable before screening."""
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
        info = probe_video(raw_path)
        if not info or info["duration"] < 2.5 or info["width"] < 320:
            raw_path.unlink(missing_ok=True)
            return None
        return info
    except Exception:
        raw_path.unlink(missing_ok=True)
        return None


def _transcode_to_mp4(raw_path: Path, info: dict, dest_mp4: Path) -> Optional[dict]:
    """Transcode approved raw clip (up to 25s slice) into clean yuv420p H.264 MP4."""
    try:
        start_at = 1.5 if info["duration"] > 18.0 else 0.0
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_at:.2f}",
                "-i",
                str(raw_path),
                "-t",
                "25",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
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


def download_and_normalize_clip(
    candidate: VideoCandidate,
    dest_mp4: Path,
    max_download_mb: int = 32,
) -> Optional[dict]:
    """Download candidate video and normalize to clean H.264 MP4 (max 25s slice) for fast rendering."""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    raw_path = dest_mp4.with_suffix(".raw_dl")
    info = _download_raw_clip(candidate, raw_path, max_download_mb=max_download_mb)
    if not info:
        return None
    return _transcode_to_mp4(raw_path, info, dest_mp4)


def _build_frame_strip(clip: Path, out_jpg: Path) -> Optional[Path]:
    """Grab 3 frames (15% / 50% / 85%) of a clip and tile them horizontally for vision screening."""
    try:
        from PIL import Image

        info = probe_video(clip)
        if not info:
            return None
        dur = max(1.0, float(info["duration"]))
        frames = []
        for i, frac in enumerate((0.15, 0.5, 0.85)):
            fp = out_jpg.with_name(f"{out_jpg.stem}_f{i}.jpg")
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{dur * frac:.2f}", "-i", str(clip), "-frames:v", "1",
                 "-vf", "scale=-2:360", "-q:v", "4", str(fp)],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if fp.exists():
                frames.append(Image.open(fp).convert("RGB"))
                fp.unlink(missing_ok=True)
        if not frames:
            return None
        total_w = sum(f.width for f in frames) + 8 * (len(frames) - 1)
        strip = Image.new("RGB", (total_w, max(f.height for f in frames)), (0, 0, 0))
        x = 0
        for f in frames:
            strip.paste(f, (x, 0))
            x += f.width + 8
        strip.save(out_jpg, quality=85)
        return out_jpg
    except Exception:
        return None


def vision_screen_clip(clip: Path, scene: SceneSpec) -> tuple[bool, bool, str]:
    """Ask Gemini Vision whether a clip is family-safe, relevant, and free of watermarks/UI overlays.

    Returns (accepted, safe_but_weak, reason). If vision screening is unavailable, falls back to accepting
    (metadata blocklist has already been applied).
    """
    if os.environ.get("ASSET_VISION_SCREEN", "true").lower() != "true":
        return True, True, "vision screening disabled"
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        return True, True, "no LLM API key configured; skipped vision screening"
    strip = _build_frame_strip(clip, clip.with_name(f"{clip.stem}_screen.jpg"))
    if strip is None:
        return False, False, "could not extract frames"
    import time

    # Gentle spacing keeps Vision screening below free-tier per-minute RPM bursts
    time.sleep(1.2)
    try:
        from pydantic import BaseModel, Field

        from src.providers import call_structured_agent

        class ClipScreen(BaseModel):
            family_safe: bool = Field(description="False if ANY nudity, sexual/suggestive content, gore, violence, or otherwise inappropriate imagery for a kids/teen science channel")
            relevance_score: float = Field(ge=0, le=10, description="How well the frames visually match the scene narration and visual query")
            has_watermark_or_ui: bool = Field(description="True if frames show music-player/album art, app UI, big logos, text slides, or channel watermarks")
            is_real_footage: bool = Field(description="True if real camera footage (not cartoon/animation/slide)")
            is_scientific_visualization: bool = Field(
                default=False,
                description="True if it is a serious scientific visualization (molecular/protein model, microscopy, "
                "research simulation render) - NOT a cartoon, fractal screensaver, or text slide",
            )
            reason: str = ""

        verdict = call_structured_agent(
            provider="auto",
            model=os.environ.get("GEMINI_SCREEN_MODEL", "gemini-3.5-flash-lite"),
            system_prompt=(
                f"You are a strict visual safety & relevance screener for '{get_brand_name()}', a family-friendly "
                "international everyday science & technology short-video channel. The attached image shows "
                "3 frames sampled from a candidate stock clip. Judge it strictly."
            ),
            user_payload={
                "scene_narration": scene.narration,
                "visual_query": scene.visual_query,
            },
            response_model=ClipScreen,
            handoff_path=None,
            image_paths=[strip],
        )
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        if "429" in err_str or "503" in err_str or "timed out" in err_str.lower():
            return False, True, f"vision rate-limit fallback eligible ({err_str[:80]})"
        return False, False, f"vision screening error (rejected for safety): {err_str[:120]}"
    finally:
        strip.unlink(missing_ok=True)

    min_rel = float(os.environ.get("ASSET_MIN_RELEVANCE", "5"))
    sci_ok = verdict.is_scientific_visualization and verdict.relevance_score >= 6.5
    clean = verdict.family_safe and (verdict.is_real_footage or sci_ok) and not verdict.has_watermark_or_ui
    ok = clean and verdict.relevance_score >= min_rel
    return ok, clean, (
        f"safe={verdict.family_safe} real={verdict.is_real_footage} sci={verdict.is_scientific_visualization} "
        f"ui={verdict.has_watermark_or_ui} rel={verdict.relevance_score} {verdict.reason}"
    )


def _next_free_path(assets_dir: Path, scene_id: str) -> Path:
    """Versioned filenames so re-scouting never overwrites footage used by earlier render versions."""
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
    """Assign fitting 100% copyright-free transition SFX + optional animation pop/spark SFX per scene."""
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
    has_visual_anim = bool(
        getattr(scene, "mechanism", None) is not None
        or getattr(scene, "overlay_badge", None)
    )
    if force_animation and has_visual_anim:
        if trans_cue == "spark":
            anim_cue = "pop"
        elif trans_cue == "pop":
            anim_cue = "spark"
        else:
            anim_cue = "spark" if scene_idx % 2 == 1 else "pop"

    return (
        memory.get_sfx_path(trans_cue) if trans_cue else None,
        memory.get_sfx_path(anim_cue) if anim_cue else None,
    )


_FILLER_AND_ADJECTIVES: frozenset[str] = frozenset({
    "the", "and", "with", "from", "into", "that", "this", "for", "are", "was",
    "in", "on", "of", "to", "a", "an", "at", "by", "or", "as", "is", "be",
    "has", "had", "can", "not", "but", "out", "off", "all", "its", "one", "two",
    "big", "low", "high", "use", "used", "using", "show", "showing", "video",
    "clip", "clips", "footage", "close", "closeup", "macro", "shot", "view",
    "real", "screen", "monitor", "computer", "dark", "light", "bright", "cold",
    "hot", "warm", "tiny", "small", "large", "fast", "slow", "hard", "soft",
    "red", "green", "blue", "white", "black", "orange", "yellow", "background",
    "human", "person", "people", "hand", "hands", "finger", "fingers", "inside",
    "surface", "object", "thing", "modern", "clean", "simple", "everyday", "next",
})

_DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cooktop": ("stove", "induction", "kitchen", "cooking", "pan", "pot", "burner"),
    "stove": ("cooktop", "kitchen", "cooking", "pan", "burner"),
    "induction": ("cooktop", "stove", "coil", "pan", "magnetic", "cooking"),
    "smartphone": ("phone", "mobile", "iphone", "android", "screen", "touchscreen"),
    "phone": ("smartphone", "mobile", "iphone", "display", "screen", "charger"),
    "checkout": ("payment", "terminal", "card", "credit", "nfc", "register", "store"),
    "payment": ("card", "credit", "contactless", "terminal", "phone", "pos"),
    "nfc": ("contactless", "payment", "card", "phone", "chip", "coil"),
    "oled": ("display", "screen", "smartphone", "pixel", "led", "tv"),
    "earbuds": ("earphones", "headphones", "audio", "music", "wireless"),
    "microwave": ("oven", "kitchen", "food", "cooking", "heating"),
    "fridge": ("refrigerator", "freezer", "cooling", "ice", "kitchen"),
    "refrigerator": ("fridge", "freezer", "ice", "cooling", "kitchen"),
    "teflon": ("pan", "nonstick", "skillet", "cooking", "coating", "egg"),
    "nonstick": ("pan", "frying", "skillet", "cooking", "kitchen"),
    "smartwatch": ("watch", "fitness", "wrist", "wearable", "tracker", "heart"),
    "airplane": ("aircraft", "plane", "jet", "wing", "flight", "window"),
    "elevator": ("lift", "building", "cable", "skyscraper"),
    "battery": ("charging", "charger", "power", "energy", "electric", "lithium"),
    "solar": ("panel", "photovoltaic", "sunlight", "renewable", "energy"),
    "turbine": ("wind", "blade", "generator", "power", "energy"),
    "filter": ("water", "purifier", "clean", "charcoal", "glass"),
    "bridge": ("highway", "steel", "road", "structure"),
    "plastic": ("bottle", "polymer", "packaging", "recycling"),
}

_IRRELEVANT_STOCK_NOISE: frozenset[str] = frozenset({
    "jellyfish", "cow", "cows", "penguin", "penguins", "butterfly", "butterflies",
    "snake", "python", "squirrel", "kitten", "puppy", "cat", "dog", "moose",
    "alpaca", "sheep", "goat", "horse", "flamingo", "deer", "duck", "bird",
    "fireworks", "skateboard", "skater", "wedding", "bride", "tulip", "tulips",
    "poppy", "snowdrop", "daisy", "lavender", "bacon", "beer", "cocktail",
    "pilates", "yoga", "ballet", "marbles", "antiquariat", "amethyst",
    "cream", "chocolate", "cake", "dessert", "candy",
})


def _extract_scene_topic_terms(scene: SceneSpec) -> set[str]:
    raw_blob = " ".join([scene.visual_query, *scene.fallback_queries]).lower()
    base_tokens = [
        t for t in re.findall(r"[a-z]{3,}", raw_blob)
        if t not in _FILLER_AND_ADJECTIVES
    ]
    expanded: set[str] = set(base_tokens)
    for t in base_tokens:
        for syn in _DOMAIN_SYNONYMS.get(t, ()):
            expanded.add(syn)
    return expanded


def _metadata_relevance_score(cand: VideoCandidate, scene: SceneSpec, topic_terms: set[str]) -> int:
    title_low = cand.title.lower()
    # Strip "pixabay #12345 - " prefix before checking tokens
    meta_clean = re.sub(r"^(?:pixabay|pexels)\s*#\d+\s*-\s*", "", title_low)
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
    """Scout real video footage + cached SFX for each scene (supports targeted re-scouting)."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    results: list[SceneAsset] = []
    used_urls: set[str] = set(exclude_urls or ())
    used_stems: set[str] = set()
    max_screens = int(os.environ.get("ASSET_MAX_SCREENS_PER_SCENE", "3"))
    flow_bank_clips = list_flow_clip_bank()
    used_flow_ids: set[str] = set()
    try:
        from src.tools.telegram_notify import get_owner_runtime_overrides

        _ro = get_owner_runtime_overrides()
    except Exception:
        _ro = {}
    force_trans_sfx = bool(_ro.get("force_sfx_every_transition", True))
    force_anim_sfx = bool(_ro.get("force_sfx_every_animation", True))
    owner_visual_bias = str(_ro.get("visual_style_bias") or "").strip()
    try:
        raw_used = memory.get_used_flow_clip_ids()
        if isinstance(raw_used, set):
            used_flow_ids = set(raw_used)
    except Exception:
        pass
    episode_id = assets_dir.parent.name if assets_dir.parent else ""

    total_scenes = len(scenes)
    for scene_idx, scene in enumerate(scenes):
        trans_sfx_path, anim_sfx_path = resolve_scene_sfx_cues(
            scene_idx=scene_idx,
            total_scenes=total_scenes,
            scene=scene,
            memory=memory,
            force_transition=force_trans_sfx,
            force_animation=force_anim_sfx,
        )
        if target_scene_ids is not None and scene.scene_id not in target_scene_ids and existing_assets and scene.scene_id in existing_assets:
            results.append(existing_assets[scene.scene_id])
            used_urls.add(existing_assets[scene.scene_id].source_url)
            continue

        dest_mp4 = _next_free_path(assets_dir, scene.scene_id)
        chosen_cand: Optional[VideoCandidate] = None
        chosen_info: Optional[dict] = None
        fallback_cand: Optional[VideoCandidate] = None
        fallback_info: Optional[dict] = None
        fallback_raw: Optional[Path] = None
        fallback_meta_score = 0
        screens_done = 0
        topic_terms = _extract_scene_topic_terms(scene)

        if manual_overrides and scene.scene_id in manual_overrides:
            cand = manual_overrides[scene.scene_id]
            info = download_and_normalize_clip(cand, dest_mp4)
            if info:
                chosen_cand, chosen_info = cand, info

        if chosen_cand is None and flow_bank_clips:
            flow_hit = _try_match_flow_bank_clip(
                scene=scene,
                flow_clips=flow_bank_clips,
                used_flow_ids=used_flow_ids,
                used_urls=used_urls,
                dest_mp4=dest_mp4,
                episode_id=episode_id,
                memory=memory,
            )
            if flow_hit:
                chosen_cand, chosen_info = flow_hit

        if chosen_cand is None:
            raw_queries = [scene.visual_query] + list(scene.fallback_queries)
            if owner_visual_bias:
                raw_queries = [f"{scene.visual_query} {owner_visual_bias}"] + raw_queries
            broadened: list[str] = []
            noun_anchors: list[str] = []
            for q in raw_queries:
                words = [w for w in re.findall(r"[A-Za-z]+", q.lower()) if len(w) >= 3 and w not in _FILLER_AND_ADJECTIVES]
                if len(words) >= 2:
                    broadened.append(" ".join(words[:2]))
                    broadened.append(" ".join(words[-2:]))
                for w in words:
                    if w in _DOMAIN_SYNONYMS and w not in noun_anchors:
                        noun_anchors.append(w)

            scene_text_blob = " ".join([scene.visual_query, *scene.fallback_queries, scene.narration or ""]).lower()
            is_gadget_or_home = bool(
                re.search(
                    r"\b(?:phone|smartphone|screen|oled|battery|charger|earbuds|watch|camera|"
                    r"microwave|fridge|stove|cooktop|pan|kitchen|car|ev|bridge|elevator|traffic|road|glass|"
                    r"plastic|solar|wind|airplane|vr|headset|sensor|chip|circuit|laptop|payment)\b",
                    scene_text_blob,
                )
            )
            if is_gadget_or_home:
                generic_fallbacks = [
                    "smartphone technology closeup",
                    "circuit board microchip macro",
                    "modern kitchen cooking pan",
                    "electric car city driving",
                    "engineering laboratory testing",
                ]
            else:
                generic_fallbacks = [
                    "physics laboratory experiment",
                    "engineering workshop prototype",
                    "electronic circuit macro",
                    "solar panels clean energy",
                    "science laboratory glassware",
                ]

            seen_q: set[str] = set()
            queries: list[str] = []
            for q in [*raw_queries, *broadened, *noun_anchors[:2], *generic_fallbacks]:
                q_norm = q.strip()
                if q_norm and q_norm.lower() not in seen_q:
                    seen_q.add(q_norm.lower())
                    queries.append(q_norm)

            source_rank = {"pexels": 0, "pixabay": 1, "wikimedia": 2, "nasa": 3}
            for q in queries:
                stock_cands = search_pexels(q, orientation="portrait") + search_pixabay(q)
                if len(stock_cands) < 3:
                    stock_cands += search_pexels(q, orientation="landscape")
                is_generic_q = q in generic_fallbacks
                wiki_cands = search_wikimedia_videos(q) if (len(stock_cands) < 3 or is_generic_q) else []
                q_terms = [w for w in re.findall(r"[a-z]+", q.lower()) if len(w) >= 3 and w not in _FILLER_AND_ADJECTIVES]
                if wiki_cands and not is_generic_q and q_terms:
                    matched_wiki = [
                        wc for wc in wiki_cands
                        if any(t in wc.title.lower() for t in q_terms)
                    ]
                    wiki_cands = matched_wiki
                nasa_cands = (
                    search_nasa_videos(q, limit=3)
                    if (not is_gadget_or_home and len(stock_cands) < 2 and len(wiki_cands) < 2)
                    else []
                )
                cands = stock_cands + wiki_cands + nasa_cands
                scored_cands: list[tuple[int, VideoCandidate]] = []
                for cand in cands:
                    m_score = _metadata_relevance_score(cand, scene, topic_terms)
                    if m_score < 0:
                        continue
                    # On specific queries, skip completely un-tagged / 0-overlap Pixabay hits BEFORE spending Vision quota!
                    if not is_generic_q and m_score == 0 and cand.source == "pixabay":
                        continue
                    scored_cands.append((m_score, cand))

                scored_cands.sort(
                    key=lambda pair: (
                        -pair[0],
                        source_rank.get(pair[1].source, 9),
                        0 if pair[1].width >= 720 else 1,
                        -min(pair[1].width, 1920),
                    )
                )

                per_query_screens = 0
                for m_score, cand in scored_cands:
                    stem = cand.title.lower()[:32]
                    if cand.download_url in used_urls or cand.page_url in used_urls or stem in used_stems:
                        continue
                    if screens_done >= max_screens or per_query_screens >= 2:
                        break
                    raw_path = dest_mp4.with_suffix(".raw_dl")
                    raw_info = _download_raw_clip(cand, raw_path)
                    used_urls.add(cand.download_url)
                    used_urls.add(cand.page_url)
                    used_stems.add(stem)
                    if not raw_info:
                        continue
                    screens_done += 1
                    per_query_screens += 1
                    ok, clean, reason = vision_screen_clip(raw_path, scene)
                    is_rate_limit = "rate-limit" in reason.lower()
                    print(f"[asset_scout] {scene.scene_id} screen (meta={m_score}) '{cand.title[:56]}': ok={ok} ({reason[:145]})")
                    # If Vision API was temporarily rate-limited BUT metadata strongly matches scene keywords (>=2), accept cleanly!
                    if not ok and is_rate_limit and m_score >= 2:
                        ok = True
                        print(f"[asset_scout] {scene.scene_id}: promoted strong lexical match (meta={m_score}) during Vision 429")
                    if ok:
                        chosen_info = _transcode_to_mp4(raw_path, raw_info, dest_mp4)
                        if chosen_info:
                            chosen_cand = cand
                            if fallback_raw is not None:
                                fallback_raw.unlink(missing_ok=True)
                                fallback_raw = None
                            break
                        continue
                    # Keep fallback only if lexical match exists OR Vision itself verified clean on a generic tech/lab query
                    eligible_fb = clean and (m_score >= 1 or (not is_rate_limit and is_generic_q))
                    if eligible_fb and (fallback_cand is None or m_score > fallback_meta_score):
                        if fallback_raw is not None:
                            fallback_raw.unlink(missing_ok=True)
                        fb_raw = dest_mp4.with_name(f"{dest_mp4.stem}_fb.raw")
                        raw_path.replace(fb_raw)
                        fallback_cand, fallback_info, fallback_raw, fallback_meta_score = cand, raw_info, fb_raw, m_score
                    else:
                        raw_path.unlink(missing_ok=True)
                if chosen_cand or screens_done >= max_screens:
                    break

            if chosen_cand is None and fallback_cand is not None and fallback_raw is not None:
                norm_info = _transcode_to_mp4(fallback_raw, fallback_info or {}, dest_mp4)
                if norm_info:
                    chosen_cand, chosen_info = fallback_cand, norm_info
                    print(f"[asset_scout] {scene.scene_id}: memakai fallback aman (meta={fallback_meta_score})")

        if fallback_raw is not None and fallback_raw.exists():
            fallback_raw.unlink(missing_ok=True)

        if (chosen_cand is None or chosen_info is None) and existing_assets and scene.scene_id in existing_assets:
            print(f"[asset_scout] {scene.scene_id}: tidak ada pengganti yang lolos screening; memakai footage sebelumnya")
            results.append(existing_assets[scene.scene_id])
            continue

        if chosen_cand is None or chosen_info is None:
            raise RuntimeError(
                f"Agent 6 could not find real video footage for {scene.scene_id} "
                f"(queries: {[scene.visual_query] + list(scene.fallback_queries)})"
            )

        results.append(
            SceneAsset(
                scene_id=scene.scene_id,
                footage_path=str(dest_mp4),
                source=chosen_cand.source,
                source_url=chosen_cand.page_url,
                license=chosen_cand.license,
                duration_sec=round(chosen_info["duration"], 2),
                width=chosen_info["width"],
                height=chosen_info["height"],
                sfx_path=trans_sfx_path,
                animation_sfx_path=anim_sfx_path,
                notes=chosen_cand.title,
            )
        )

    if tried_urls_out is not None:
        tried_urls_out.update(used_urls)
    return Agent6Output(assets=results, bgm_path=memory.get_bgm_path())
