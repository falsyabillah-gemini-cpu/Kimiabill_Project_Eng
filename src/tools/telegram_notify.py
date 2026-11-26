"""Telegram Bot notification, interactive chatbot & owner-feedback compiler.

Behavior:
1. Automated episodes publish on schedule (no blocking approval wait).
2. Rendered MP4 previews, weekly audit PDFs, and Google Flow prompts are delivered
   straight to the owner's Telegram chat (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).
3. Any feedback written by the owner in Telegram is stored into `data/owner_feedback.json`
   (either instantly via Google Apps Script -> GitHub `repository_dispatch` OR via `getUpdates` polling),
   compiled into per-agent directives (Agents 1, 2, 3, 5, 6, 7, 8) + deterministic runtime engine overrides.
4. Supports `/rules`, `/clear_rules`, `/rerender <notes>`, `/feedback <notes>`, or plain text feedback.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from src.brand_config import get_brand_name

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
OWNER_FEEDBACK_PATH = BASE_DIR / "data" / "owner_feedback.json"

DEFAULT_RUNTIME_OVERRIDES: dict[str, Any] = {
    "force_sfx_every_transition": True,
    "force_sfx_every_animation": True,
    "sfx_gain_db_delta": 0.0,
    "bgm_gain_db_delta": 0.0,
    "voice_speed_delta_pct": 0,
    "subtitle_scale_delta": 0.0,
    "visual_style_bias": "",
    "target_duration_sec": None,
}


def _brand_tag() -> str:
    return f"[{get_brand_name()}]"


def _bot_creds() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def telegram_enabled() -> bool:
    token, chat_id = _bot_creds()
    return bool(token and chat_id)


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def send_telegram_message(text: str, chat_id_override: Optional[str] = None) -> bool:
    """Send plain-text Telegram message(s), splitting cleanly below Telegram's 4096-char limit."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    if not token or not chat_id:
        return False
    body = (text or "").strip()
    if not body:
        return False

    chunks: list[str] = []
    remaining = body
    while len(remaining) > 3900:
        split_at = remaining.rfind("\n", 0, 3900)
        if split_at < 500:
            split_at = 3900
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)

    ok_all = True
    for chunk in chunks:
        try:
            resp = requests.post(
                _api_url(token, "sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": False,
                },
                timeout=25,
            )
            data = resp.json() if resp.status_code == 200 else {}
            if not data.get("ok"):
                ok_all = False
                print(f"[telegram] sendMessage failed ({resp.status_code}): {resp.text[:160]}")
        except Exception as exc:
            ok_all = False
            print(f"[telegram] sendMessage error: {exc}")
    return ok_all


def send_telegram_video(
    mp4_path: Path,
    caption: str = "",
    chat_id_override: Optional[str] = None,
) -> bool:
    """Upload MP4 video directly to Telegram chat (<= 48 MB), falling back to message if oversized."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    if not token or not chat_id:
        return False
    if not mp4_path.exists():
        return send_telegram_message(caption, chat_id_override=chat_id) if caption else False
    size_mb = mp4_path.stat().st_size / (1024 * 1024)
    if size_mb > 48.0:
        print(f"[telegram] Video {mp4_path.name} ({size_mb:.1f} MB > 48 MB); sending message link only.")
        return send_telegram_message(caption, chat_id_override=chat_id)

    short_cap = (caption or "").strip()[:980]
    try:
        with mp4_path.open("rb") as fh:
            resp = requests.post(
                _api_url(token, "sendVideo"),
                data={
                    "chat_id": chat_id,
                    "caption": short_cap,
                    "supports_streaming": "true",
                },
                files={"video": (mp4_path.name, fh, "video/mp4")},
                timeout=120,
            )
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("ok"):
            return True
        print(f"[telegram] sendVideo failed ({resp.status_code}): {resp.text[:160]}; fallback to sendMessage.")
    except Exception as exc:
        print(f"[telegram] sendVideo error: {exc}; fallback to sendMessage.")
    return send_telegram_message(caption, chat_id_override=chat_id) if caption else False


def send_telegram_document(
    file_path: Path,
    caption: str = "",
    chat_id_override: Optional[str] = None,
) -> bool:
    """Upload PDF/TXT document directly to Telegram chat."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    if not token or not chat_id or not file_path.exists():
        return False
    if file_path.stat().st_size > 48 * 1024 * 1024:
        return False
    short_cap = (caption or "").strip()[:980]
    try:
        with file_path.open("rb") as fh:
            resp = requests.post(
                _api_url(token, "sendDocument"),
                data={"chat_id": chat_id, "caption": short_cap},
                files={"document": (file_path.name, fh)},
                timeout=90,
            )
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("ok"):
            return True
        print(f"[telegram] sendDocument failed ({resp.status_code}): {resp.text[:160]}")
    except Exception as exc:
        print(f"[telegram] sendDocument error: {exc}")
    return False


def load_owner_feedback_state() -> dict[str, Any]:
    base = {
        "last_update_id": 0,
        "items": [],
        "agent_directives": {},
        "runtime_overrides": dict(DEFAULT_RUNTIME_OVERRIDES),
    }
    if not OWNER_FEEDBACK_PATH.exists():
        return base
    try:
        raw = json.loads(OWNER_FEEDBACK_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("last_update_id", 0)
            raw.setdefault("items", [])
            raw.setdefault("agent_directives", {})
            ro = dict(DEFAULT_RUNTIME_OVERRIDES)
            if isinstance(raw.get("runtime_overrides"), dict):
                ro.update(raw["runtime_overrides"])
            raw["runtime_overrides"] = ro
            return raw
    except Exception:
        pass
    return base


def save_owner_feedback_state(state: dict[str, Any]) -> None:
    OWNER_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_FEEDBACK_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_deterministic_overrides_and_notes(text: str) -> tuple[dict[str, Any], dict[str, list[str]], list[str]]:
    """Deterministic fallback + hard engine guardrails for bilingual (EN/ID) owner Telegram feedback."""
    low = (text or "").lower()
    overrides: dict[str, Any] = {}
    directives: dict[str, list[str]] = {}
    notes: list[str] = []

    if any(k in low for k in ("sfx", "sound effect", "efek suara", "whoosh", "pop", "transition", "animation", "animasi", "ganti footage")):
        overrides["force_sfx_every_transition"] = True
        overrides["force_sfx_every_animation"] = True
        directives.setdefault("agent_3", []).append(
            "REQUIRED: set a non-'none' copyright-free `sfx_cue` (whoosh/riser/pop/spark/ding/chime) on EVERY scene, especially scene cuts and mechanism animations."
        )
        directives.setdefault("agent_5", []).append(
            "Verify every scene transition and mechanistic animation includes crisp, balanced copyright-free SFX."
        )
        notes.append("Audio Engine: Copyright-free SFX locked ON for all transitions & animations")

    if "sfx" in low and any(k in low for k in ("quiet", "quieter", "lower", "soft", "kecil", "pelan", "turun", "kurangin")):
        overrides["sfx_gain_db_delta"] = -2.5
        notes.append("Audio Engine: SFX volume lowered by -2.5 dB")
    elif "sfx" in low and any(k in low for k in ("loud", "louder", "boost", "higher", "besar", "keras", "naik", "kencang")):
        overrides["sfx_gain_db_delta"] = 2.0
        notes.append("Audio Engine: SFX volume boosted by +2.0 dB")

    if any(k in low for k in ("music", "bgm", "backsound", "musik")) and any(k in low for k in ("quiet", "quieter", "lower", "soft", "kecil", "pelan", "turun", "kebesaran", "berisik")):
        overrides["bgm_gain_db_delta"] = -3.0
        notes.append("Audio Engine: BGM lowered by -3.0 dB for clearer narration")
    elif any(k in low for k in ("music", "bgm", "backsound", "musik")) and any(k in low for k in ("loud", "louder", "boost", "besar", "naik", "keras")):
        overrides["bgm_gain_db_delta"] = 2.5
        notes.append("Audio Engine: BGM boosted by +2.5 dB")

    if any(k in low for k in ("voice", "narration", "pace", "speed", "tempo", "suara", "ngomong", "narasi")) and any(k in low for k in ("too fast", "slower", "slow down", "terlalu cepat", "kecepetan", "pelan", "lambat")):
        overrides["voice_speed_delta_pct"] = -6
        notes.append("Voice Engine: narration pace slowed by -6%")
    elif any(k in low for k in ("voice", "narration", "pace", "speed", "tempo", "suara", "ngomong", "narasi")) and any(k in low for k in ("too slow", "faster", "speed up", "terlalu lambat", "kelamaan", "cepatin", "lebih cepat")):
        overrides["voice_speed_delta_pct"] = 6
        notes.append("Voice Engine: narration pace sped up by +6%")

    if any(k in low for k in ("subtitle", "subtitles", "caption", "text", "teks", "tulisan")) and any(k in low for k in ("small", "smaller", "big", "bigger", "large", "kecil", "kebesaran", "besar")):
        if any(k in low for k in ("smaller", "too big", "shrink", "kebesaran", "terlalu besar", "kecilkan")):
            overrides["subtitle_scale_delta"] = -0.006
            notes.append("Visual Engine: subtitle font size slightly reduced")
        else:
            overrides["subtitle_scale_delta"] = 0.006
            notes.append("Visual Engine: subtitle font size increased for readability")

    m_dur = re.search(r"\b(3[0-9]|4[0-9]|5[0-9])\s*(detik|dtk|s|sec|seconds?)\b", low)
    if m_dur:
        sec = int(m_dur.group(1))
        lo_s = max(28, sec - 5)
        hi_s = min(58, sec + 4)
        overrides["target_duration_sec"] = [lo_s, hi_s]
        directives.setdefault("agent_3", []).append(
            f"Target total narration duration MUST be ~{lo_s}-{hi_s} seconds per owner directive."
        )
        notes.append(f"Engine & Agent 3: target duration adjusted to {lo_s}-{hi_s}s")

    if any(k in low for k in ("footage", "visual", "clip", "klip", "gambar", "animation", "animasi", "diagram")):
        directives.setdefault("agent_6", []).append(f"[OWNER DIRECTIVE]: {text[:260]}")

    if any(k in low for k in ("title", "judul", "thumbnail", "cover", "caption", "comment", "komen", "hashtag")):
        directives.setdefault("agent_7", []).append(f"[OWNER DIRECTIVE]: {text[:260]}")

    return overrides, directives, notes


def compile_owner_feedback_state(use_llm: bool = True) -> dict[str, Any]:
    """Compile all saved owner feedback items into concrete directives for Agents 1,2,3,5,6,7,8 + runtime overrides."""
    st = load_owner_feedback_state()
    items = [x for x in (st.get("items") or []) if isinstance(x, dict) and str(x.get("text") or "").strip()]

    ro = dict(DEFAULT_RUNTIME_OVERRIDES)
    merged_dirs: dict[str, list[str]] = {f"agent_{n}": [] for n in (1, 2, 3, 5, 6, 7, 8)}
    compile_notes: list[str] = []

    if not items:
        st["agent_directives"] = merged_dirs
        st["runtime_overrides"] = ro
        save_owner_feedback_state(st)
        return {"state": st, "notes": []}

    for it in items:
        d_ro, d_dirs, d_notes = _extract_deterministic_overrides_and_notes(str(it.get("text") or ""))
        for k, v in d_ro.items():
            if v not in (None, ""):
                ro[k] = v
        for ak, lst in d_dirs.items():
            for line in lst:
                if line not in merged_dirs.setdefault(ak, []):
                    merged_dirs[ak].append(line)
        for n in d_notes:
            if n not in compile_notes:
                compile_notes.append(n)

    if use_llm:
        try:
            from pydantic import BaseModel, Field
            from src.providers import call_structured_agent

            class _CompiledFeedbackPlan(BaseModel):
                summary_text: str = Field(description="1-2 sentence summary of applied changes for owner Telegram confirmation")
                agent_1: list[str] = Field(default_factory=list, description="Concrete directives for Agent 1 (Ideation/Topics)")
                agent_2: list[str] = Field(default_factory=list, description="Concrete directives for Agent 2 (Idea Selection & Hook)")
                agent_3: list[str] = Field(default_factory=list, description="Concrete directives for Agent 3 (Script, Duration, Diagram & SFX)")
                agent_5: list[str] = Field(default_factory=list, description="Concrete directives for Agent 5 (Video QA Auditor)")
                agent_6: list[str] = Field(default_factory=list, description="Concrete directives for Agent 6 (Real Footage Scout)")
                agent_7: list[str] = Field(default_factory=list, description="Concrete directives for Agent 7 (Title, Thumbnail, Caption & Pinned Comment)")
                agent_8: list[str] = Field(default_factory=list, description="Concrete directives for Agent 8 (Weekly Audit & Topic Map)")
                visual_style_bias: str = Field(default="", description="Short English visual query bias for Agent 6 if relevant, else ''")

            brand_name = get_brand_name()
            compiled = call_structured_agent(
                provider="auto",
                model="gemini-3.8-flash",
                system_prompt=(
                    f"You are the Studio Coordinator for '{brand_name}', translating natural-language owner feedback from Telegram "
                    "(which may be written in Indonesian or English) into crisp, actionable operational directives in English "
                    "for Agents 1, 2, 3, 5, 6, 7, and 8.\n"
                    "Read `owner_feedback_items` and populate ONLY the relevant agent arrays with concise directives. "
                    "Never invent rules the owner didn't ask for; if feedback mentions SFX, remind Agents 3 & 5 that the studio "
                    "uses internal 100% copyright-free synthesized SFX (whoosh, pop, spark, ding, chime, riser)."
                ),
                user_payload={
                    "owner_feedback_items": items[-8:],
                    "existing_runtime_overrides": ro,
                },
                response_model=_CompiledFeedbackPlan,
            )
            for ak in ("agent_1", "agent_2", "agent_3", "agent_5", "agent_6", "agent_7", "agent_8"):
                for line in getattr(compiled, ak, []) or []:
                    clean_l = " ".join(str(line).strip().split())
                    if clean_l and clean_l not in merged_dirs[ak]:
                        merged_dirs[ak].append(clean_l)
            if (compiled.visual_style_bias or "").strip():
                ro["visual_style_bias"] = compiled.visual_style_bias.strip()[:160]
            if (compiled.summary_text or "").strip():
                compile_notes.insert(0, compiled.summary_text.strip())
        except Exception as exc:
            print(f"[telegram] LLM feedback compile skipped (deterministic rules applied): {exc}")

    for it in reversed(items[-5:]):
        raw_txt = str(it.get("text") or "").strip()
        ts = str(it.get("created_at_wib") or "").strip()
        tag = f"[OWNER FEEDBACK • {ts}]: {raw_txt}"
        for ak in ("agent_1", "agent_2", "agent_3", "agent_5", "agent_7", "agent_8"):
            if not any(raw_txt[:40].lower() in existing.lower() for existing in merged_dirs[ak]):
                merged_dirs[ak].append(tag)

    st["agent_directives"] = {k: v[:10] for k, v in merged_dirs.items()}
    st["runtime_overrides"] = ro
    save_owner_feedback_state(st)
    return {"state": st, "notes": compile_notes}


def record_owner_feedback(
    text: str,
    mode: str = "next_video",
    target_episode_id: str = "",
    update_id: Optional[int] = None,
    compile_agents: bool = True,
    use_llm_compiler: bool = True,
) -> dict[str, Any]:
    """Persist owner feedback from Telegram and compile it directly into all Agents + runtime engine overrides."""
    clean = " ".join((text or "").strip().split())
    st = load_owner_feedback_state()
    if update_id is not None and int(update_id) > int(st.get("last_update_id") or 0):
        st["last_update_id"] = int(update_id)
    if not clean:
        save_owner_feedback_state(st)
        return st

    items: list[dict[str, Any]] = [x for x in (st.get("items") or []) if isinstance(x, dict)]
    if not any(str(x.get("text", "")).strip().lower() == clean.lower() and x.get("mode") == mode for x in items[-5:]):
        now_wib = datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
        items.append(
            {
                "created_at_wib": now_wib,
                "mode": mode,
                "target_episode_id": target_episode_id,
                "text": clean[:500],
            }
        )
    st["items"] = items[-20:]
    save_owner_feedback_state(st)
    if compile_agents:
        res = compile_owner_feedback_state(use_llm=use_llm_compiler)
        return res["state"]
    return st


def clear_owner_feedback() -> None:
    st = load_owner_feedback_state()
    st["items"] = []
    st["agent_directives"] = {f"agent_{n}": [] for n in (1, 2, 3, 5, 6, 7, 8)}
    st["runtime_overrides"] = dict(DEFAULT_RUNTIME_OVERRIDES)
    save_owner_feedback_state(st)


def get_owner_runtime_overrides() -> dict[str, Any]:
    st = load_owner_feedback_state()
    ro = dict(DEFAULT_RUNTIME_OVERRIDES)
    if isinstance(st.get("runtime_overrides"), dict):
        ro.update(st["runtime_overrides"])
    return ro


def get_active_owner_feedback_directives(limit: int = 6) -> list[str]:
    """Return owner feedback lines ordered most recent first, including any immediate override env."""
    out: list[str] = []
    override = " ".join(os.environ.get("OWNER_FEEDBACK_OVERRIDE", "").strip().split())
    if override:
        out.append(f"[PRIORITY OWNER FEEDBACK - RERENDER]: {override}")
    st = load_owner_feedback_state()
    for item in reversed((st.get("items") or [])[-limit:]):
        txt = str(item.get("text") or "").strip()
        if not txt:
            continue
        ts = str(item.get("created_at_wib") or "").strip()
        mode_tag = "RERENDER" if item.get("mode") == "rerender" else "NEXT VIDEO"
        out.append(f"[OWNER FEEDBACK ({mode_tag} • {ts})]: {txt}")
    return out


def get_agent_specific_owner_directives(agent_key: str, limit: int = 8) -> list[str]:
    """Return compiled agent-specific directives merged with global owner feedback."""
    st = load_owner_feedback_state()
    if st.get("items") and not (st.get("agent_directives") or {}).get(agent_key):
        try:
            st = compile_owner_feedback_state(use_llm=False)["state"]
        except Exception:
            pass
    out: list[str] = []
    override = " ".join(os.environ.get("OWNER_FEEDBACK_OVERRIDE", "").strip().split())
    if override:
        out.append(f"[PRIORITY OWNER FEEDBACK - RERENDER]: {override}")
    for line in (st.get("agent_directives") or {}).get(agent_key, []):
        s = str(line).strip()
        if s and s not in out:
            out.append(s)
    for raw_line in get_active_owner_feedback_directives(limit=4):
        if raw_line not in out:
            out.append(raw_line)
    return out[:limit]


def format_active_owner_rules_text() -> str:
    st = load_owner_feedback_state()
    items = st.get("items") or []
    ro = get_owner_runtime_overrides()
    lines = [f"📋 {_brand_tag()} ACTIVE OWNER RULES & FEEDBACK ACROSS ALL AGENTS:"]
    if not items:
        lines.append("• No custom text feedback queued yet.")
    else:
        for i, it in enumerate(items[-6:], 1):
            lines.append(f"{i}. ({it.get('created_at_wib', '-')}) {it.get('text', '')}")
    lines.extend(
        [
            "",
            "⚙️ CURRENT ENGINE RUNTIME OVERRIDES:",
            f"• Transition SFX (scene cuts): {'ON' if ro.get('force_sfx_every_transition', True) else 'AUTO'}",
            f"• Animation/Badge SFX: {'ON' if ro.get('force_sfx_every_animation', True) else 'AUTO'}",
            f"• Volume Delta SFX: {ro.get('sfx_gain_db_delta', 0.0):+.1f} dB | BGM: {ro.get('bgm_gain_db_delta', 0.0):+.1f} dB",
            f"• Voice Speed Delta: {int(ro.get('voice_speed_delta_pct', 0)):+d}%",
        ]
    )
    if ro.get("target_duration_sec"):
        lines.append(f"• Custom Duration Target: {ro['target_duration_sec'][0]}-{ro['target_duration_sec'][1]}s")
    lines.append("\nSend /clear_rules to reset extra overrides.")
    return "\n".join(lines)


def handle_telegram_dispatch_feedback(text: str, mode: str = "next_video") -> dict[str, Any]:
    """Process an incoming webhook dispatch (normal feedback, /rules, or /clear_rules) directly to Agents 1-8."""
    clean = " ".join((text or "").strip().split())
    low = clean.lower()
    if not clean:
        return {"ok": True, "action": "empty"}

    if low.startswith("/rules") or low == "/aturan":
        msg = format_active_owner_rules_text()
        send_telegram_message(msg)
        return {"ok": True, "action": "rules_listed"}

    if low.startswith("/clear_rules") or low.startswith("/clear"):
        clear_owner_feedback()
        send_telegram_message(f"🧹 {_brand_tag()} All extra owner rules reset to studio defaults (transition & animation SFX stay ON).")
        return {"ok": True, "action": "rules_cleared"}

    is_question_only = (
        mode == "next_video"
        and "?" in clean
        and any(low.startswith(q) for q in ("kenapa", "mengapa", "gimana", "bagaimana", "apakah", "bisakah", "kapan", "why ", "how ", "when ", "can you"))
    )
    if is_question_only:
        send_telegram_message(
            f"ℹ️ {_brand_tag()} Pesan ini terdeteksi sebagai pertanyaan status (tidak disimpan sebagai rule permanen).\n"
            f"• Untuk render & upload video sekarang, kirim: `/rerender <catatan opsional>`\n"
            f"• Untuk menambah rule produksi, tulis perintah tanpa tanda tanya (contoh: `Volume BGM lebih pelan`)."
        )
        return {"ok": True, "action": "question_ignored"}

    record_owner_feedback(clean, mode=mode, compile_agents=False)
    comp = compile_owner_feedback_state(use_llm=True)
    notes = comp.get("notes") or []
    ro = (comp.get("state") or {}).get("runtime_overrides") or {}
    summary_bullets = "\n".join(f"• {n}" for n in notes[:4]) if notes else "• Saved & injected directly into Agents 1, 2, 3, 5, 6, 7, and 8."
    send_telegram_message(
        "\n".join(
            [
                f"🧠 {_brand_tag()} Applied Automatically to All Agents (1, 2, 3, 5, 6, 7, 8 + Engine):",
                summary_bullets,
                f"🎛️ Engine: transition SFX={'ON' if ro.get('force_sfx_every_transition', True) else 'AUTO'}, animation SFX={'ON' if ro.get('force_sfx_every_animation', True) else 'AUTO'}",
                "Send /rules to view active overrides.",
            ]
        )
    )
    return {"ok": True, "action": "compiled", "notes": notes}


def sync_telegram_feedback_updates() -> dict[str, Any]:
    """Poll new Telegram owner messages when no Apps Script webhook is registered."""
    token, allowed_chat = _bot_creds()
    if not token or not allowed_chat:
        return {"ok": False, "skipped": "not_configured"}

    try:
        wh = requests.get(_api_url(token, "getWebhookInfo"), timeout=10).json()
        if wh.get("ok") and (wh.get("result") or {}).get("url"):
            return {"ok": True, "skipped": "webhook_active"}
    except Exception:
        pass

    st = load_owner_feedback_state()
    offset = int(st.get("last_update_id") or 0) + 1
    try:
        resp = requests.get(
            _api_url(token, "getUpdates"),
            params={"offset": offset, "timeout": 1, "limit": 20},
            timeout=15,
        )
        data = resp.json() if resp.status_code == 200 else {}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if not data.get("ok"):
        return {"ok": False, "error": data}

    saved_count = 0
    max_uid = int(st.get("last_update_id") or 0)
    for upd in data.get("result") or []:
        uid = int(upd.get("update_id") or 0)
        if uid > max_uid:
            max_uid = uid
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        if chat_id != str(allowed_chat):
            continue
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        if not text:
            continue
        low = text.lower()
        if low in ("/start", "/status", "/help"):
            continue
        if low.startswith("/rules") or low == "/aturan":
            send_telegram_message(format_active_owner_rules_text())
            continue
        if low.startswith("/clear"):
            clear_owner_feedback()
            send_telegram_message(f"🧹 {_brand_tag()} Owner feedback queue cleared.")
            continue
        if low.startswith("/rerender"):
            note = text[len("/rerender"):].strip()
            if note:
                record_owner_feedback(note, mode="rerender", update_id=uid, use_llm_compiler=True)
                saved_count += 1
                send_telegram_message(f"🔄 {_brand_tag()} Rerender directive compiled to Agents 1-8 & applied to this run: {note}")
            continue
        if low.startswith("/feedback"):
            text = text[len("/feedback"):].strip()
        if text:
            record_owner_feedback(text, mode="next_video", update_id=uid, use_llm_compiler=True)
            saved_count += 1
            send_telegram_message(f"✅ {_brand_tag()} Feedback compiled to Agents 1-8:\n\"{text}\"")

    if max_uid > int(st.get("last_update_id") or 0):
        st_latest = load_owner_feedback_state()
        st_latest["last_update_id"] = max_uid
        save_owner_feedback_state(st_latest)
    return {"ok": True, "saved_count": saved_count, "last_update_id": max_uid}
