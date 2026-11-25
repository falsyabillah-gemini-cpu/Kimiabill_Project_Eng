"""Agent 7 Tool: Multi-Platform Social Media Publisher (YouTube Shorts, Instagram Reels, TikTok).

Supports:
- Safe `dry_run` mode (builds thumbnail + SEO copy package without uploading).
- Safe `draft` mode (YouTube `unlisted`, TikTok `inbox/draft`).
- Full `public` mode for 24/7 hands-off automation once credentials are configured in `.env`.
- Optional 1-API Social Bridge (`SOCIAL_BRIDGE_WEBHOOK_URL`) for Upload-Post / Ayrshare / Late.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal

import requests

from src.state import Agent7Output, PlatformCopy, PlatformPublishResult


PublishMode = Literal["dry_run", "draft", "public"]


def _format_full_caption(copy: PlatformCopy) -> str:
    tags = " ".join(copy.hashtags)
    return f"{copy.caption}\n\n{tags}".strip()


def _publish_youtube_shorts(
    video_path: Path,
    thumbnail_path: Path | None,
    copy: PlatformCopy,
    mode: PublishMode,
) -> PlatformPublishResult:
    if mode == "dry_run":
        return PlatformPublishResult(
            platform="youtube_shorts",
            mode=mode,
            status="packaged_only",
            detail="Dry-run: YouTube Shorts title, description, and thumbnail packaged.",
        )

    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return PlatformPublishResult(
            platform="youtube_shorts",
            mode=mode,
            status="skipped_missing_creds",
            detail="Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN in .env/Secrets for YouTube Shorts auto-upload.",
        )

    # 1. Exchange refresh token for access token
    tok_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    tok_resp.raise_for_status()
    access_token = tok_resp.json()["access_token"]
    auth_hdr = {"Authorization": f"Bearer {access_token}"}

    # 2. Initiate resumable upload to YouTube Data API v3
    privacy = "public" if mode == "public" else "unlisted"
    metadata = {
        "snippet": {
            "title": copy.title[:95],
            "description": _format_full_caption(copy),
            "tags": [t.lstrip("#") for t in copy.hashtags],
            "categoryId": "28",  # Science & Technology
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    init_resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={**auth_hdr, "Content-Type": "application/json; charset=UTF-8"},
        json=metadata,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    # 3. Upload video binary
    with open(video_path, "rb") as vf:
        up_resp = requests.put(
            upload_url,
            headers={**auth_hdr, "Content-Type": "video/mp4"},
            data=vf,
            timeout=300,
        )
    up_resp.raise_for_status()
    vid_id = up_resp.json()["id"]

    # 4. Upload custom thumbnail if available
    if thumbnail_path and thumbnail_path.exists():
        with open(thumbnail_path, "rb") as tf:
            requests.post(
                f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid_id}",
                headers={**auth_hdr, "Content-Type": "image/jpeg"},
                data=tf,
                timeout=60,
            )

    return PlatformPublishResult(
        platform="youtube_shorts",
        mode=mode,
        status="published" if mode == "public" else "uploaded_draft",
        post_id=vid_id,
        post_url=f"https://youtube.com/shorts/{vid_id}",
        detail=f"Uploaded to YouTube Shorts (privacy={privacy}, category=Science & Technology).",
    )


def _publish_instagram_reels(
    video_path: Path,
    thumbnail_path: Path | None,
    copy: PlatformCopy,
    mode: PublishMode,
) -> PlatformPublishResult:
    if mode == "dry_run":
        return PlatformPublishResult(
            platform="instagram_reels",
            mode=mode,
            status="packaged_only",
            detail="Dry-run: caption, hashtag, dan cover Instagram Reels siap diunggah.",
        )

    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.environ.get("INSTAGRAM_IG_USER_ID")
    public_base = os.environ.get("PUBLIC_MEDIA_BASE_URL", "").rstrip("/")
    if not (token and ig_user_id and public_base):
        return PlatformPublishResult(
            platform="instagram_reels",
            mode=mode,
            status="skipped_missing_creds",
            detail="Isi INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_IG_USER_ID, dan PUBLIC_MEDIA_BASE_URL di .env untuk auto-upload IG Reels.",
        )

    video_url = f"{public_base}/{video_path.name}"
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": _format_full_caption(copy),
        "share_to_feed": "true",
        "access_token": token,
    }
    if thumbnail_path and thumbnail_path.exists():
        payload["cover_url"] = f"{public_base}/{thumbnail_path.name}"

    c_resp = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        data=payload,
        timeout=60,
    )
    c_resp.raise_for_status()
    creation_id = c_resp.json()["id"]

    # Poll processing status up to 90s
    for _ in range(18):
        st = requests.get(
            f"https://graph.facebook.com/v21.0/{creation_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=20,
        ).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram container error: {st}")
        time.sleep(5)

    if mode != "public":
        return PlatformPublishResult(
            platform="instagram_reels",
            mode=mode,
            status="uploaded_draft",
            post_id=creation_id,
            detail="IG Reels container siap (mode=draft; belum di-publish ke feed publik).",
        )

    pub_resp = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=60,
    )
    pub_resp.raise_for_status()
    media_id = pub_resp.json()["id"]
    return PlatformPublishResult(
        platform="instagram_reels",
        mode=mode,
        status="published",
        post_id=media_id,
        detail="Berhasil publish Instagram Reels.",
    )


def _publish_tiktok(
    video_path: Path,
    copy: PlatformCopy,
    mode: PublishMode,
) -> PlatformPublishResult:
    if mode == "dry_run":
        return PlatformPublishResult(
            platform="tiktok",
            mode=mode,
            status="packaged_only",
            detail="Dry-run: caption, hashtag, dan cover TikTok siap diunggah.",
        )

    token = os.environ.get("TIKTOK_ACCESS_TOKEN")
    if not token:
        return PlatformPublishResult(
            platform="tiktok",
            mode=mode,
            status="skipped_missing_creds",
            detail="Isi TIKTOK_ACCESS_TOKEN di .env (atau gunakan SOCIAL_BRIDGE_WEBHOOK_URL) untuk auto-upload TikTok.",
        )

    file_size = video_path.stat().st_size
    endpoint = (
        "https://open.tiktokapis.com/v2/post/publish/video/init/"
        if mode == "public"
        else "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
    )
    body = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        }
    }
    if mode == "public":
        body["post_info"] = {
            "title": _format_full_caption(copy)[:2200],
            "privacy_level": "PUBLIC_TO_EVERYONE",
        }

    init_resp = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=body,
        timeout=30,
    )
    init_resp.raise_for_status()
    data = init_resp.json().get("data", {})
    upload_url = data["upload_url"]
    publish_id = data.get("publish_id")

    with open(video_path, "rb") as vf:
        put_resp = requests.put(
            upload_url,
            headers={
                "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                "Content-Type": "video/mp4",
            },
            data=vf,
            timeout=300,
        )
    put_resp.raise_for_status()
    return PlatformPublishResult(
        platform="tiktok",
        mode=mode,
        status="published" if mode == "public" else "uploaded_draft",
        post_id=publish_id,
        detail="Berhasil kirim ke TikTok (" + ("Direct Publish" if mode == "public" else "Draft Inbox") + ").",
    )


def publish_to_all_platforms(
    video_path: Path,
    agent7_pkg: Agent7Output,
    mode: PublishMode = "draft",
) -> list[PlatformPublishResult]:
    """Execute social publishing across all configured platforms (or unified bridge webhook)."""
    thumb = Path(agent7_pkg.thumbnail_path) if agent7_pkg.thumbnail_path else None
    bridge_url = os.environ.get("SOCIAL_BRIDGE_WEBHOOK_URL")
    bridge_key = os.environ.get("SOCIAL_BRIDGE_API_KEY")

    if bridge_url and mode != "dry_run":
        resp = requests.post(
            bridge_url,
            headers={"Authorization": f"Bearer {bridge_key or ''}", "Content-Type": "application/json"},
            json={
                "mode": mode,
                "video_path": str(video_path),
                "thumbnail_path": str(thumb) if thumb else None,
                "copies": [c.model_dump() for c in agent7_pkg.platform_copies],
            },
            timeout=120,
        )
        resp.raise_for_status()
        return [
            PlatformPublishResult(
                platform=c.platform,
                mode=mode,
                status="published" if mode == "public" else "uploaded_draft",
                detail=f"Dikirim melalui Social Bridge ({bridge_url}).",
            )
            for c in agent7_pkg.platform_copies
        ]

    results: list[PlatformPublishResult] = []
    for copy in agent7_pkg.platform_copies:
        try:
            if copy.platform == "youtube_shorts":
                results.append(_publish_youtube_shorts(video_path, thumb, copy, mode))
            elif copy.platform == "instagram_reels":
                results.append(_publish_instagram_reels(video_path, thumb, copy, mode))
            elif copy.platform == "tiktok":
                results.append(_publish_tiktok(video_path, copy, mode))
        except Exception as exc:
            results.append(
                PlatformPublishResult(
                    platform=copy.platform,
                    mode=mode,
                    status="error",
                    detail=str(exc),
                )
            )
    return results
