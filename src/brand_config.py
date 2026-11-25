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


def get_brand_name() -> str:
    env_override = os.environ.get("BRAND_NAME", "").strip()
    if env_override:
        return env_override
    return str(load_brand_yaml().get("brand", {}).get("name", "Bill_Explains")).strip()


def get_brand_handle() -> str:
    env_override = os.environ.get("BRAND_HANDLE", "").strip()
    if env_override:
        return env_override
    handle = str(load_brand_yaml().get("brand", {}).get("handle", "@bill_explains")).strip()
    return handle if handle.startswith("@") else f"@{handle}"


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
    return ["04:00", "21:00"]


def get_watermark_path() -> Path:
    assets = BASE_DIR / "assets"
    custom = assets / "watermark.png"
    if custom.exists():
        return custom
    legacy = assets / f"watermark_{get_brand_slug()}.png"
    return legacy
