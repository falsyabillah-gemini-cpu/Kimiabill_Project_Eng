"""Centralized dynamic brand loader so changing channel name/handle only requires editing config/brand.yaml."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_brand_yaml() -> dict[str, Any]:
    path = BASE_DIR / "config" / "brand.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def clear_brand_cache() -> None:
    load_brand_yaml.cache_clear()


def get_brand_display_name() -> str:
    env_override = os.environ.get("BRAND_NAME", "").strip()
    if env_override:
        return env_override
    b = load_brand_yaml().get("brand", {})
    return str(b.get("brand_display_name") or b.get("name") or "Bill_Explains").strip()


def get_brand_name() -> str:
    return get_brand_display_name()


def get_brand_spoken_name() -> str:
    env_override = os.environ.get("BRAND_SPOKEN_NAME", "").strip()
    if env_override:
        return env_override
    b = load_brand_yaml().get("brand", {})
    raw = str(b.get("brand_spoken_name") or get_brand_display_name()).strip()
    return " ".join(raw.replace("_", " ").replace("@", "").split()) or "Bill Explains"


def get_brand_handle() -> str:
    env_override = os.environ.get("BRAND_HANDLE", "").strip()
    if env_override:
        return env_override if env_override.startswith("@") else f"@{env_override}"
    b = load_brand_yaml().get("brand", {})
    handle = str(b.get("brand_handle") or b.get("handle") or "@Bill_explains12").strip()
    return handle if handle.startswith("@") else f"@{handle}"


def get_youtube_cta() -> str:
    b = load_brand_yaml().get("brand", {})
    cta = str(b.get("youtube_cta") or f"Subscribe to {get_brand_spoken_name()}").strip()
    return cta


def get_platform_cta(platform: str = "youtube_shorts") -> str:
    b = load_brand_yaml().get("brand", {})
    ctas = b.get("platform_ctas") or {}
    if isinstance(ctas, dict) and ctas.get(platform):
        return str(ctas[platform]).strip()
    if platform == "youtube_shorts":
        return get_youtube_cta()
    return f"Follow {get_brand_spoken_name()}"


def get_brand_slug() -> str:
    raw = get_brand_handle().lstrip("@").lower()
    return "".join(ch for ch in raw if ch.isalnum() or ch in ("_", "-")) or "scitech"


def get_brand_tagline() -> str:
    return str(load_brand_yaml().get("brand", {}).get("tagline", "EVERYDAY SCI & TECH")).strip()


def get_disciplines() -> list[str]:
    discs = load_brand_yaml().get("brand", {}).get("disciplines")
    if isinstance(discs, list) and discs:
        return [str(d).strip().lower() for d in discs]
    return ["gadgets", "engineering", "materials", "energy", "biotech"]


def get_gdrive_root() -> str:
    env_root = os.environ.get("GDRIVE_ROOT_FOLDER", "").strip()
    if env_root:
        return env_root
    return str(load_brand_yaml().get("brand", {}).get("gdrive_root_folder", "EverydaySciTech_Studio")).strip()


def get_daily_slots_wib() -> list[str]:
    slots = load_brand_yaml().get("schedule", {}).get("daily_slots_wib")
    if isinstance(slots, list) and len(slots) >= 2:
        return [str(s).strip() for s in slots[:2]]
    return ["21:00", "04:00"]


def get_watermark_path() -> Path:
    assets = BASE_DIR / "assets"
    custom = assets / "watermark.png"
    if custom.exists():
        return custom
    legacy = assets / f"watermark_{get_brand_slug()}.png"
    return legacy
