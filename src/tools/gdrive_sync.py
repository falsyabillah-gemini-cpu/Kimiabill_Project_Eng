"""Google Drive uploader + email notifier for Kimiabill Studio episodes.

Supports two backends automatically:
1. Google Apps Script Web App (`GDRIVE_WEBHOOK_URL`) — works on GitHub Actions, VPS/Docker,
   and local machines without Google Cloud Console setup; uploads directly to your personal
   Google Drive and sends email notification via Gmail (`MailApp`).
2. Local `gdrive` CLI fallback (when running inside Antigravity VM).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import requests

from src.brand_config import get_brand_name, get_brand_slug, get_gdrive_root

DEFAULT_NOTIFY_EMAIL = "researchfalsyabillah@gmail.com"


def _run_gdrive(args: list[str]) -> str:
    proc = subprocess.run(["gdrive", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gdrive {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc.stdout.strip()


def ensure_folder(name: str, parent_id: Optional[str] = None) -> str:
    search_args = ["readonly", "search", "--name-exact", name, "--type", "folder", "--json"]
    if parent_id:
        search_args.extend(["--parent-id", parent_id])
    raw = _run_gdrive(search_args)
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = items.get("files", [])
        if items:
            return items[0]["id"]
    except json.JSONDecodeError:
        pass

    mkdir_args = ["mutate", "mkdir", name]
    if parent_id:
        mkdir_args.extend(["--parent", parent_id])
    _run_gdrive(mkdir_args)

    raw_after = _run_gdrive(search_args)
    items_after = json.loads(raw_after)
    if isinstance(items_after, dict):
        items_after = items_after.get("files", [])
    if not items_after:
        raise RuntimeError(f"Created folder '{name}' but could not locate its ID.")
    return items_after[0]["id"]


def _build_captions_summary(episode_dir: Path) -> tuple[str, str]:
    """Read Agent 3/7 handoffs to build a clean title and ready-to-copy captions text."""
    title = episode_dir.name
    lines: list[str] = []

    a3_path = episode_dir / "handoffs" / "agent3_script.json"
    if a3_path.exists():
        try:
            a3 = json.loads(a3_path.read_text(encoding="utf-8"))
            title = a3.get("title") or title
        except Exception:
            pass

    a7_path = episode_dir / "handoffs" / "agent7_package.json"
    if a7_path.exists():
        try:
            a7 = json.loads(a7_path.read_text(encoding="utf-8"))
            for copy in a7.get("platform_copies", []):
                plat = str(copy.get("platform", "")).upper().replace("_", " ")
                tags = " ".join(copy.get("hashtags", []))
                lines.append(f"[{plat}]")
                lines.append(f"Judul: {copy.get('title', '')}")
                lines.append(f"Caption:\n{copy.get('caption', '')}")
                if tags:
                    lines.append(f"Hashtag: {tags}")
                if copy.get("pinned_comment"):
                    lines.append(f"Pinned Comment: {copy.get('pinned_comment')}")
                lines.append("")
        except Exception:
            pass

    return title, "\n".join(lines).strip()


def _normalize_webhook_url(raw: str) -> str:
    """Accept full https://.../exec URL, scheme-less script.google.com URL, or raw Deployment ID (AKfy...)."""
    url = raw.strip().strip('"').strip("'")
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("script.google.com/"):
        return f"https://{url}"
    if "/" not in url and len(url) >= 20:
        # User pasted the Google Apps Script Deployment ID (e.g. AKfycb...)
        return f"https://script.google.com/macros/s/{url}/exec"
    return f"https://{url}"


def _resolve_youtube_webhook(default_url: str) -> tuple[str, Optional[str]]:
    """Return (youtube_webhook_url, token_override), falling back to Drive webhook when unset."""
    yt_url = os.environ.get("YOUTUBE_WEBHOOK_URL", "").strip()
    if yt_url:
        yt_tok = os.environ.get("YOUTUBE_WEBHOOK_TOKEN", "").strip() or None
        return yt_url, yt_tok
    return default_url, None


def _post_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    timeout: int = 180,
    token_override: Optional[str] = None,
) -> dict[str, Any]:
    import time

    webhook_url = _normalize_webhook_url(webhook_url)
    token = token_override if token_override is not None else os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
    if token:
        payload = {**payload, "token": token}
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout, allow_redirects=True)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Google Drive webhook error: {data}")
            return data
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < 3:
                wait_s = attempt * 8
                print(f"[gdrive] Webhook transient error ({exc}); retry {attempt}/2 dalam {wait_s}s...")
                time.sleep(wait_s)
            else:
                raise
    raise last_exc  # pragma: no cover


def _extract_youtube_copy(episode_dir: Path, fallback_title: str) -> dict[str, Any]:
    """Extract YouTube Shorts title, description (including references), and tags."""
    refs: list[str] = []
    state_path = episode_dir / "episode_state.json"
    if state_path.exists():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            refs = (st.get("render_plan") or {}).get("references") or []
        except Exception:
            pass
    ref_block = ""
    if refs:
        ref_lines = "\n".join(f"- {r}" for r in refs)
        ref_block = f"\n\nSources & References:\n{ref_lines}"

    brand_slug = get_brand_slug()
    a7_path = episode_dir / "handoffs" / "agent7_package.json"
    if a7_path.exists():
        try:
            a7 = json.loads(a7_path.read_text(encoding="utf-8"))
            for copy in a7.get("platform_copies", []):
                if copy.get("platform") == "youtube_shorts":
                    tags = [str(t).lstrip("#") for t in copy.get("hashtags", [])]
                    tag_str = " ".join(copy.get("hashtags", []))
                    desc = f"{copy.get('caption', '')}{ref_block}\n\n{tag_str}".strip()
                    return {
                        "title": copy.get("title") or fallback_title,
                        "description": desc,
                        "tags": tags or ["science", "technology", brand_slug, "shorts"],
                    }
        except Exception:
            pass
    return {
        "title": fallback_title,
        "description": f"{fallback_title}{ref_block}\n\n#science #technology #{brand_slug} #shorts",
        "tags": ["science", "technology", brand_slug, "shorts"],
    }


def _ensure_apps_script_mp4_size(mp4_path: Path, max_bytes: int = 34 * 1024 * 1024) -> Path:
    """If MP4 exceeds Apps Script payload limit, transcode a target-bitrate 1080p copy (<32 MB) so Drive & YouTube upload never skip."""
    if mp4_path.stat().st_size <= max_bytes:
        return mp4_path
    orig_mb = mp4_path.stat().st_size / (1024 * 1024)
    dur_sec = 65.0
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            dur_sec = max(10.0, float(probe.stdout.strip()))
    except Exception:
        pass
    target_mb = min(30.0, (max_bytes / (1024 * 1024)) * 0.85)
    total_kbps = int((target_mb * 1024 * 8) / dur_sec)
    video_kbps = min(6000, max(350, total_kbps - 128))
    compressed = mp4_path.with_name(f"{mp4_path.stem}_web{mp4_path.suffix}")
    print(
        f"[gdrive] {mp4_path.name} ({orig_mb:.1f} MB > {max_bytes / (1024 * 1024):.0f} MB limit); "
        f"auto-compressing 1080p -> ~{target_mb:.0f} MB ({video_kbps}k) agar tetap ter-upload ke Drive & YouTube..."
    )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(mp4_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            f"{video_kbps}k",
            "-maxrate",
            f"{int(video_kbps * 1.15)}k",
            "-bufsize",
            f"{video_kbps * 2}k",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(compressed),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and compressed.exists() and compressed.stat().st_size <= 35_500_000:
        print(f"[gdrive] Auto-compress berhasil: {compressed.stat().st_size / (1024 * 1024):.1f} MB")
        return compressed
    return mp4_path


def _upload_via_webhook(
    episode_dir: Path,
    episode_name: str,
    root_folder_name: str,
    webhook_url: str,
    allow_youtube: bool = True,
    qa_summary: str = "",
) -> str:
    """Upload final video, thumbnail, and caption sheet via Google Apps Script webhook + YouTube + email notify."""
    init_res = _post_webhook(
        webhook_url,
        {
            "action": "init_episode",
            "root_folder": root_folder_name,
            "episode_name": episode_name,
        },
        timeout=60,
    )
    folder_id = init_res["folder_id"]
    folder_url = init_res["folder_url"]

    title, captions_text = _build_captions_summary(episode_dir)
    if captions_text:
        caption_file = episode_dir / "CAPTION_SIAP_UPLOAD.txt"
        caption_file.write_text(f"Judul: {title}\n\n{captions_text}\n", encoding="utf-8")

    video_file_id: Optional[str] = None
    video_url: Optional[str] = None
    preview_mp4_path: Optional[Path] = None
    thumbnail_file_id: Optional[str] = None
    thumbnail_url: Optional[str] = None

    upload_patterns = (
        "FINAL_*.mp4",
        "THUMBNAIL_*.jpg",
        "CAPTION_SIAP_UPLOAD.txt",
        "*_contact_sheet.jpg",
    )
    for pattern in upload_patterns:
        for f in sorted(episode_dir.glob(pattern)):
            upload_file = f
            if f.suffix.lower() == ".mp4" and not f.stem.endswith("_web") and f.stat().st_size > 34 * 1024 * 1024:
                upload_file = _ensure_apps_script_mp4_size(f, max_bytes=34 * 1024 * 1024)
            elif f.stem.endswith("_web"):
                continue
            # Safety cap in case non-MP4 or fallback still exceeds 36 MB Apps Script limit
            if upload_file.stat().st_size > 36 * 1024 * 1024:
                print(f"[gdrive] Skipping {upload_file.name} (>36 MB Apps Script limit)")
                continue
            mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            b64 = base64.b64encode(upload_file.read_bytes()).decode("ascii")
            res = _post_webhook(
                webhook_url,
                {
                    "action": "upload_file",
                    "folder_id": folder_id,
                    "filename": f.name,
                    "mime_type": mime,
                    "content_base64": b64,
                },
                timeout=240,
            )
            if f.name.startswith("FINAL_") and f.suffix == ".mp4":
                video_file_id = res.get("file_id")
                video_url = res.get("file_url")
                preview_mp4_path = upload_file
            elif f.name.startswith("THUMBNAIL_"):
                thumbnail_file_id = res.get("file_id")
                thumbnail_url = res.get("file_url")

    # Auto-upload to YouTube Shorts via Apps Script ONLY when QA gate passed & PUBLISH_MODE is draft/public
    publish_mode = os.environ.get("PUBLISH_MODE", "draft").strip().lower()
    youtube_url: Optional[str] = None
    youtube_privacy: Optional[str] = None
    youtube_note: Optional[str] = None

    # Read requester metadata if fulfilling an audience comment request
    req_by: Optional[str] = None
    req_comment_id: Optional[str] = None
    pinned_comment_txt: Optional[str] = None
    state_json = episode_dir / "episode_state.json"
    if state_json.exists():
        try:
            st_data = json.loads(state_json.read_text(encoding="utf-8"))
            req_by = st_data.get("requested_by")
            req_comment_id = st_data.get("request_comment_id")
        except Exception:
            pass
    a7_json = episode_dir / "handoffs" / "agent7_package.json"
    if a7_json.exists():
        try:
            a7_data = json.loads(a7_json.read_text(encoding="utf-8"))
            for cp in a7_data.get("platform_copies", []):
                if cp.get("platform") == "youtube_shorts" and cp.get("pinned_comment"):
                    pinned_comment_txt = str(cp.get("pinned_comment")).strip()
                    break
        except Exception:
            pass

    if not allow_youtube:
        youtube_note = f"DITAHAN OLEH AGENT 5 QA AUDITOR (tidak di-upload ke YouTube): {qa_summary}"
        print(f"[youtube] {youtube_note}")
    elif video_file_id and publish_mode in ("draft", "public"):
        yt_copy = _extract_youtube_copy(episode_dir, title)
        privacy = "public" if publish_mode == "public" else "unlisted"
        yt_payload: dict[str, Any] = {
            "action": "upload_youtube",
            "video_file_id": video_file_id,
            "thumbnail_file_id": thumbnail_file_id,
            "title": yt_copy["title"],
            "description": yt_copy["description"],
            "tags": yt_copy["tags"],
            "privacy": privacy,
        }
        if privacy == "public":
            first_cmt = pinned_comment_txt or (
                f"Thanks {req_by} for requesting this topic! 🙌 Were you surprised by how this works? Drop another everyday tech mystery below!"
                if req_by
                else "Be honest — did you know how this actually works? 👇 What everyday gadget or tech should we break down next?"
            )
            yt_payload["first_comment"] = first_cmt
            if req_comment_id and req_by:
                yt_payload["reply_to_comment_id"] = req_comment_id
                yt_payload["reply_comment_text"] = (
                    f"Hey {req_by}! Your requested topic just went live on {get_brand_name()} 🎉 Check it out 👉 {{YOUTUBE_URL}}"
                )
        try:
            import time as _time

            yt_webhook_url, yt_token = _resolve_youtube_webhook(webhook_url)
            yt_res: dict[str, Any] = {}
            yt_last_exc: Optional[Exception] = None
            for yt_try in range(1, 3):
                try:
                    yt_res = _post_webhook(
                        yt_webhook_url,
                        yt_payload,
                        timeout=240,
                        token_override=yt_token,
                    )
                    yt_last_exc = None
                    break
                except Exception as _exc:
                    yt_last_exc = _exc
                    print(f"[youtube] Percobaan upload #{yt_try}/2 gagal ({_exc}); mencoba ulang dalam 15 detik...")
                    if yt_try < 2:
                        _time.sleep(15)
            if yt_last_exc is not None:
                raise yt_last_exc
            youtube_url = yt_res.get("youtube_url")
            youtube_privacy = yt_res.get("privacy", privacy)
            video_id_yt = yt_res.get("video_id")
            if not video_id_yt and youtube_url:
                video_id_yt = youtube_url.rstrip("/").split("/")[-1]

            thumbnail_set = bool(yt_res.get("thumbnail_set"))
            thumbnail_error = yt_res.get("thumbnail_error") or ""
            if thumbnail_file_id and not thumbnail_set and video_id_yt:
                import time

                for th_try in range(2):
                    time.sleep(12)
                    try:
                        th_res = _post_webhook(
                            yt_webhook_url,
                            {
                                "action": "set_thumbnail",
                                "video_id": video_id_yt,
                                "thumbnail_file_id": thumbnail_file_id,
                            },
                            timeout=60,
                            token_override=yt_token,
                        )
                        if th_res.get("thumbnail_set"):
                            thumbnail_set = True
                            thumbnail_error = ""
                            break
                        thumbnail_error = th_res.get("error") or thumbnail_error
                        if th_res.get("processing_status") != "processing" and "processing" not in thumbnail_error.lower():
                            break
                    except Exception as th_exc:
                        thumbnail_error = str(th_exc)
                        break

            comment_posted = bool(yt_res.get("comment_posted"))
            comment_error = yt_res.get("comment_error") or ""
            if privacy == "public" and not comment_posted and yt_payload.get("first_comment") and video_id_yt:
                import time

                print(f"[youtube] Komentar pertama belum masuk saat upload ({comment_error or 'processing'}); mencoba ulang 10 detik lagi...")
                time.sleep(10)
                try:
                    c_res = _post_webhook(
                        yt_webhook_url,
                        {
                            "action": "post_comment",
                            "video_id": video_id_yt,
                            "comment_text": yt_payload["first_comment"],
                        },
                        timeout=60,
                        token_override=yt_token,
                    )
                    if c_res.get("ok"):
                        comment_posted = True
                        comment_error = ""
                except Exception as c_exc:
                    comment_error = str(c_exc)
            print(
                f"[youtube] Berhasil upload ke YouTube Shorts ({youtube_privacy}): {youtube_url} "
                f"(thumbnail_set={thumbnail_set}, comment_posted={comment_posted}, replied_requester={yt_res.get('replied_requester')}"
                f"{', thumbnail_error=' + thumbnail_error if thumbnail_error else ''}"
                f"{', comment_error=' + comment_error if comment_error else ''})"
            )
            try:
                hf_dir = episode_dir / "handoffs"
                hf_dir.mkdir(parents=True, exist_ok=True)
                (hf_dir / "youtube_upload.json").write_text(
                    json.dumps(
                        {
                            "youtube_url": youtube_url,
                            "privacy": youtube_privacy,
                            "thumbnail_set": thumbnail_set,
                            "thumbnail_error": thumbnail_error or None,
                            "comment_posted": comment_posted,
                            "comment_error": comment_error or None,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
        except Exception as exc:
            youtube_note = f"Belum ter-upload otomatis ({exc})"
            print(f"[youtube] Peringatan: gagal upload YouTube via webhook ({exc})")
    elif publish_mode == "dry_run":
        youtube_note = "Mode dry_run (hanya simpan ke Drive; ubah publish_mode ke draft/public untuk auto-upload YouTube)."

    notify_to = os.environ.get("NOTIFY_EMAIL_TO", DEFAULT_NOTIFY_EMAIL).strip() or DEFAULT_NOTIFY_EMAIL
    model_block = ""
    try:
        from src.providers import format_model_usage_summary

        model_block = format_model_usage_summary()
    except Exception:
        model_block = ""

    sheet_id = os.environ.get("STUDIO_SHEET_ID", "").strip()
    if not sheet_id:
        try:
            import yaml

            brand_cfg = yaml.safe_load((episode_dir.parent.parent / "config" / "brand.yaml").read_text(encoding="utf-8"))
            sheet_id = str(brand_cfg.get("publishing", {}).get("tracking_sheet_id") or "").strip()
        except Exception:
            sheet_id = ""
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit" if sheet_id else ""

    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_wib_str = datetime.now(ZoneInfo("Asia/Jakarta")).isoformat(timespec="seconds")
    ep_disc = "-"
    ep_sec_tag = "EVERYDAY_TECH"
    ep_dur: Any = "-"
    ep_qa: Any = "-"
    if state_json.exists():
        try:
            st_data = json.loads(state_json.read_text(encoding="utf-8"))
            rp = st_data.get("render_plan") or {}
            ep_disc = rp.get("discipline") or "-"
            ep_sec_tag = rp.get("section_tag") or "EVERYDAY_TECH"
            ep_dur = rp.get("total_duration_sec") or "-"
            hist5 = st_data.get("agent5_history") or []
            if hist5:
                ep_qa = hist5[-1].get("overall_score") or "-"
        except Exception:
            pass

    model_short = "-"
    try:
        from src.providers import get_model_usage_summary

        ms = get_model_usage_summary()
        if ms:
            model_short = " | ".join(f"{k.split(' ')[1]}: {v}" for k, v in ms.items())
    except Exception:
        pass

    sheet_row = [
        now_wib_str,
        episode_name,
        ep_sec_tag,
        title,
        ep_disc,
        ep_dur,
        ep_qa,
        youtube_privacy or publish_mode,
        youtube_url or "-",
        folder_url,
        model_short,
        req_by or "-",
        f"Email to {notify_to}",
        "-",
    ] if sheet_id else []

    tg_sent = False
    notify_email_also = os.environ.get("NOTIFY_EMAIL_ALSO", "true").strip().lower() == "true"
    brand_upper = get_brand_name().upper()
    try:
        from src.tools.telegram_notify import send_telegram_message, send_telegram_video, telegram_enabled

        if telegram_enabled():
            yt_line = f"▶️ YouTube ({youtube_privacy or 'public'}): {youtube_url}" if youtube_url else f"⏳ YouTube: {youtube_note or '-'}"
            tg_caption = "\n".join(
                [
                    f"🎬 [{brand_upper}] NEW VIDEO READY ({ep_sec_tag})",
                    f"Title: {title}",
                    f"Episode: {episode_name} | Pillar: {ep_disc} | QA: {ep_qa}/10",
                    yt_line,
                    f"📁 Drive: {folder_url}",
                    *( [f"📊 Sheet: {sheet_url}"] if sheet_url else [] ),
                    "",
                    "💬 Feedback for NEXT VIDEO? Reply directly here.",
                    "🔄 Need a full redo? Reply: /rerender <notes>",
                ]
            )
            if preview_mp4_path and preview_mp4_path.exists():
                tg_sent = send_telegram_video(preview_mp4_path, caption=tg_caption)
            else:
                tg_sent = send_telegram_message(tg_caption)
            if tg_sent and captions_text:
                send_telegram_message(f"📝 [{brand_upper}] Caption & Hashtags ({episode_name}):\n\n{captions_text[:3200]}")
            print(f"[telegram] Preview video & episode status sent to Telegram (ok={tg_sent})")
    except Exception as tg_exc:
        print(f"[telegram] Warning: Telegram delivery failed (email fallback): {tg_exc}")

    skip_email = bool(tg_sent and not notify_email_also)
    notif_status = "Telegram to owner" if skip_email else (f"Telegram + Email to {notify_to}" if tg_sent else f"Email to {notify_to}")
    if sheet_row:
        sheet_row[-2] = notif_status

    sections = []
    if sheet_url:
        sections.append(f"📊 Google Sheet Tracker: {sheet_url}")
    if req_by:
        sections.append(f"[FULFILLING VIEWER REQUEST]\nRequester: {req_by} (mentioned in video, caption & reply!)")
    if qa_summary:
        sections.append(f"[AGENT 5 QA SUMMARY]\n{qa_summary}")
    if model_block:
        sections.append(model_block)
    if captions_text:
        sections.append(captions_text[:3000])
    email_preview = "\n\n".join(sections).strip()
    notify_payload: dict[str, Any] = {
        "action": "notify_email",
        "to": notify_to,
        "skip_email": skip_email,
        "episode_name": episode_name,
        "title": f"[{get_brand_name()}] {title}",
        "folder_url": folder_url,
        "video_url": video_url,
        "thumbnail_url": thumbnail_url,
        "youtube_url": youtube_url,
        "youtube_privacy": youtube_privacy,
        "youtube_note": youtube_note,
        "captions_preview": email_preview,
    }
    if sheet_id:
        notify_payload["sheet_id"] = sheet_id
        notify_payload["sheet_row"] = sheet_row
    try:
        _post_webhook(
            webhook_url,
            notify_payload,
            timeout=60,
        )
        if skip_email:
            print("[gdrive] Google Sheet Tracker updated (email skipped as already delivered via Telegram)")
        else:
            print(f"[gdrive] Email notification{' & Sheet Tracker update' if sheet_id else ''} sent to {notify_to}")
    except Exception as exc:
        print(f"[gdrive] Warning: Drive upload succeeded, but webhook notification failed ({exc})")

    return folder_url


def can_upload_to_gdrive() -> bool:
    return bool(os.environ.get("GDRIVE_WEBHOOK_URL", "").strip() or shutil.which("gdrive"))


def upload_episode_to_gdrive(
    episode_dir: Path,
    episode_name: str,
    root_folder_name: Optional[str] = None,
    allow_youtube: bool = True,
    qa_summary: str = "",
) -> str:
    """Upload final video, thumbnail, and logs to Google Drive (webhook or gdrive CLI)."""
    root_name = root_folder_name or get_gdrive_root()
    webhook_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if webhook_url:
        return _upload_via_webhook(
            episode_dir,
            episode_name,
            root_name,
            webhook_url,
            allow_youtube=allow_youtube,
            qa_summary=qa_summary,
        )

    root_id = ensure_folder(root_name)
    ep_folder_id = ensure_folder(episode_name, parent_id=root_id)
    logs_folder_id = ensure_folder("01_agent_logs", parent_id=ep_folder_id)
    assets_folder_id = ensure_folder("02_real_footage", parent_id=ep_folder_id)

    for pattern in ("FINAL_*.mp4", "THUMBNAIL_*.jpg", "*_contact_sheet.jpg", "episode_state.json"):
        for f in sorted(episode_dir.glob(pattern)):
            _run_gdrive(["mutate", "upload", str(f), "--parent", ep_folder_id])

    handoffs_dir = episode_dir / "handoffs"
    if handoffs_dir.exists():
        for f in sorted(handoffs_dir.glob("*.json")):
            _run_gdrive(["mutate", "upload", str(f), "--parent", logs_folder_id])

    assets_dir = episode_dir / "assets"
    if assets_dir.exists():
        for f in sorted(assets_dir.glob("*.mp4")):
            _run_gdrive(["mutate", "upload", str(f), "--parent", assets_folder_id])

    url = _run_gdrive(["readonly", "url", ep_folder_id])
    return url.splitlines()[-1].strip()
