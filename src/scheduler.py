"""24/7 Automated Scheduler & Human-in-the-Loop Audit Gate Orchestrator.

Schedule (Asia/Jakarta, WIB):
- Slot 1 (default DAILY_VIDEO_COUNT=1): Produce 19:30 WIB -> Interactive Audit -> Reminder 20:30 WIB -> AI Fallback 20:45 WIB -> Publish 21:00 WIB (09:00 EST).
- Slot 2 (when DAILY_VIDEO_COUNT=2): Produce 20:15 WIB -> Interactive Audit -> Reminder 22:00 WIB -> AI Fallback 22:30 WIB -> Publish 04:00 WIB (16:00 EST).

All generated videos upload as `unlisted` first (`PUBLISH_MODE=draft`, `ALLOW_DIRECT_PUBLIC=false`)
and transition to scheduled/public ONLY upon explicit Owner Approval or AI Chief Editor APPROVE.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.brand_config import get_brand_name, get_daily_slots_wib
from src.pipeline import (
    BASE_DIR,
    episode_paths,
    load_configs,
    load_dotenv,
    load_or_init_state,
    prepare_episode_thumbnail,
    rescout_affected_scenes,
    run_agent_1_to_3,
    run_agent_4_proxy_and_gate0,
    run_agent_5_video_qa,
    run_agent_6_scout,
    run_agent_7_publish,
    run_final_hd_render,
    run_upload,
    save_state,
)
from src.tools.backup_queue import (
    load_backup_queue,
    peek_backup_count,
    pop_next_backup_video,
    push_backup_video,
)
from src.tools.gdrive_sync import can_upload_to_gdrive
from src.tools.pending_review import (
    compute_target_publish_iso,
    get_pending_review,
    load_pending_reviews,
    mark_review_status,
    upsert_pending_review,
)
from src.tools.social_publisher import delete_youtube_video, schedule_or_publish_youtube_video
from src.tools.telegram_notify import (
    disable_audit_keyboard,
    send_interactive_audit_message,
    send_telegram_message,
)
from src.tools.trend_scout import add_rejected_topic

WIB = ZoneInfo("Asia/Jakarta")


def _extract_affected_scenes(audit, valid_scene_ids: list[str]) -> list[str]:
    found: list[str] = []
    valid_set = set(valid_scene_ids)
    for sc in audit.affected_scenes or []:
        sc_norm = sc.strip().lower().replace(" ", "_")
        if sc_norm in valid_set and sc_norm not in found:
            found.append(sc_norm)
    blob = " ".join((audit.issues or []) + (audit.patch_instructions or []))
    for m in re.finditer(r"scene[\s_]*(\d+)", blob, flags=re.IGNORECASE):
        sc_norm = f"scene_{m.group(1)}"
        if sc_norm in valid_set and sc_norm not in found:
            found.append(sc_norm)
    return found


def _is_late_slot(hour: int) -> bool:
    return hour >= 12


def _resolve_slot(slot_override: Optional[str] = None) -> str:
    if slot_override in ("slot_2100", "slot_0400"):
        return slot_override
    slot_env = os.environ.get("STUDIO_SLOT", "").strip()
    if slot_env in ("slot_2100", "slot_0400"):
        return slot_env
    target_pub = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
    if ":" in target_pub:
        try:
            hr = int(target_pub.split(":", 1)[0])
            return "slot_0400" if hr < 12 else "slot_2100"
        except Exception:
            pass
    now_hr = datetime.now(WIB).hour
    return "slot_0400" if now_hr < 10 else "slot_2100"


def _tg_status(message: str) -> None:
    """Best-effort live Telegram status update during automated production (never raises)."""
    try:
        from src.tools.telegram_notify import send_telegram_message, telegram_enabled

        if telegram_enabled():
            send_telegram_message(message)
    except Exception:
        pass


def _summarize_footage_tiers(assets: list[Any]) -> str:
    if not assets:
        return "Tier 1"
    counts = Counter(str(getattr(a, "footage_tier", "tier_1_stock_video") or "tier_1_stock_video") for a in assets)
    parts = [f"{k}={v}" for k, v in counts.items()]
    return ", ".join(parts)


def run_one_automated_episode(
    topic_hint: Optional[str] = None,
    slot_override: Optional[str] = None,
    target_publish_iso_override: Optional[str] = None,
    revision_count_override: int = 0,
    extra_avoid_titles: Optional[list[str]] = None,
    save_as_backup_override: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    load_dotenv()
    brand_name = get_brand_name()
    slot = _resolve_slot(slot_override)
    target_publish_iso = target_publish_iso_override or compute_target_publish_iso(slot)
    save_as_backup = (
        save_as_backup_override
        if save_as_backup_override is not None
        else os.environ.get("SAVE_AS_BACKUP", "false").strip().lower() == "true"
    )

    # Hard Rule: Automated runs ALWAYS produce as unlisted draft first unless explicitly dry_run
    requested_mode = os.environ.get("PUBLISH_MODE", "draft").strip().lower()
    if requested_mode != "dry_run":
        os.environ["PUBLISH_MODE"] = "draft"
        os.environ["ALLOW_DIRECT_PUBLIC"] = "false"

    try:
        from src.tools.telegram_notify import sync_telegram_feedback_updates

        sync_res = sync_telegram_feedback_updates()
        if sync_res.get("saved_count"):
            print(f"[telegram] Synced {sync_res['saved_count']} new owner feedback note(s)!")
    except Exception as exc:
        print(f"[telegram] Feedback sync warning: {exc}")

    brand_cfg, agents_cfg = load_configs()
    if agents_cfg.get("runtime_mode") == "antigravity_local":
        print(
            "[scheduler] runtime_mode is 'antigravity_local'. "
            "Set runtime_mode: production in config/agents.yaml for 24/7 automation."
        )
        return None

    guardrails = brand_cfg.get("guardrails", {})
    max_revisions = int(guardrails.get("max_revisions", 4))
    max_topic_attempts = int(guardrails.get("max_topic_attempts", 3))
    avoid_titles: list[str] = list(extra_avoid_titles or [])

    try:
        for bk in load_backup_queue():
            bt = str(bk.get("topic_title") or "").strip()
            if bt and bt not in avoid_titles:
                avoid_titles.append(bt)
    except Exception:
        pass

    try:
        from src.tools.weekly_growth_auditor import HISTORY_PATH

        if HISTORY_PATH.exists():
            hist_items = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            for row in hist_items[-16:]:
                t = (row.get("title") or "").strip()
                if t and t not in avoid_titles:
                    avoid_titles.append(t)

        if os.environ.get("SKIP_IF_SLOT_PUBLISHED", "").lower() == "true" and not topic_hint and not save_as_backup:
            target_date = target_publish_iso[:10]
            # Check multi-slot pending_review.json first (precise per-slot & per-target-date lock)
            pr_store = load_pending_reviews().get("episodes") or {}
            for row in pr_store.values():
                if (
                    isinstance(row, dict)
                    and row.get("slot") == slot
                    and str(row.get("target_publish_iso") or "").startswith(target_date)
                    and row.get("status") in ("awaiting_review", "approved", "approved_by_ai")
                ):
                    print(
                        f"[scheduler] Slot '{slot}' already drafted/approved for {target_date} "
                        f"({row.get('episode_id')} status={row.get('status')}) — skipping duplicate cron."
                    )
                    return None
    except Exception:
        pass

    # Check if Agent 8 queued a viewer comment request to fulfill on weekday slots
    pending_req: dict | None = None
    if not topic_hint:
        try:
            from src.pipeline import _is_weekend_tech_radar_mode
            from src.tools.weekly_growth_auditor import get_next_pending_audience_request

            if not _is_weekend_tech_radar_mode():
                pending_req = get_next_pending_audience_request()
                if pending_req:
                    print(
                        f"[scheduler] Fulfilling VIEWER REQUEST from {pending_req.get('requester_name')}: "
                        f"{pending_req.get('topic_title')}"
                    )
        except Exception:
            pending_req = None

    run_t0 = time.monotonic()
    max_recovery_rounds = max(1, int(os.environ.get("MAX_RECOVERY_ROUNDS", "2")))
    max_wall_clock_sec = float(os.environ.get("MAX_PRODUCTION_WALL_SEC", str(120 * 60)))
    pub_mode_start = os.environ.get("PUBLISH_MODE", "draft").strip().lower()
    _tg_status(
        f"🎬 [{brand_name}] Starting automated production\n"
        f"• Slot: {slot} (Target publish: {target_publish_iso})\n"
        f"• Stage: Render -> Unlisted Draft -> Interactive Audit Gate\n"
        f"• Auto-retry: up to {max_recovery_rounds} rounds ({max_recovery_rounds * max_topic_attempts} attempts)"
    )

    last_err = "Unknown error"
    total_attempts_done = 0
    consecutive_quota_errors = 0

    for recovery_round in range(1, max_recovery_rounds + 1):
        for topic_attempt in range(1, max_topic_attempts + 1):
            if (time.monotonic() - run_t0) > max_wall_clock_sec:
                break
            total_attempts_done += 1
            try:
                from src.providers import reset_model_usage_log

                reset_model_usage_log()
            except Exception:
                pass
            now = datetime.now(WIB)
            ep_suffix = "" if (recovery_round == 1 and topic_attempt == 1) else f"_r{recovery_round}t{topic_attempt}"
            episode_id = f"ep_{now.strftime('%Y%m%d_%H%M%S')}{ep_suffix}"
            print(
                f"[scheduler] [{brand_name}] Starting automated production "
                f"(round {recovery_round}/{max_recovery_rounds}, attempt {topic_attempt}/{max_topic_attempts}): {episode_id}"
            )

            title = "-"
            try:
                use_req = pending_req if (recovery_round == 1 and topic_attempt == 1 and not topic_hint) else None
                state = run_agent_1_to_3(
                    episode_id,
                    topic_hint=topic_hint or (use_req.get("topic_title") if use_req else None),
                    avoid_titles=avoid_titles,
                    requested_by=use_req.get("requester_name") if use_req else None,
                    request_id=use_req.get("request_id") if use_req else None,
                    request_source_comment=use_req.get("source_comment") if use_req else None,
                    request_comment_id=use_req.get("source_comment_id") if use_req else None,
                )
                consecutive_quota_errors = 0
                title = state.agent3.title if state.agent3 else "-"
                print(f"[scheduler] Agents 1-3 complete: {title}")
                _tg_status(
                    f"📝 [{brand_name}] Script ready (Round {recovery_round}, Attempt #{topic_attempt}):\n"
                    f"\"{title}\"\n"
                    f"⏳ Scouting 3-tier visuals, TTS, animations & QA render..."
                )

                state = run_agent_6_scout(episode_id)
                print(f"[scheduler] Agent 6 complete: {len(state.agent6.assets) if state.agent6 else 0} visual assets")

                font_ratio = 0.052
                scene_offsets: dict[str, float] = {}
                qa_passed = False
                last_audit = None
                valid_scene_ids = [s.scene_id for s in (state.agent3.scenes if state.agent3 else [])]

                for version in range(1, max_revisions + 2):
                    state, proxy_video, sheet_path = run_agent_4_proxy_and_gate0(
                        episode_id,
                        version=version,
                        subtitle_font_size_ratio=font_ratio,
                        scene_offsets=scene_offsets,
                    )
                    print(
                        f"[scheduler] Agent 4 proxy v{version} + Gate 0: "
                        f"passed={state.gate0.passed if state.gate0 else False} ({proxy_video})"
                    )
                    if state.gate0 and not state.gate0.passed and any("outside target" in i for i in state.gate0.issues):
                        print(f"[scheduler] Gate 0 duration failure ({state.gate0.issues}) cannot be fixed by B-roll/subtitle edits; skipping wasted QA revisions.")
                        break

                    state, audit = run_agent_5_video_qa(episode_id, sheet_path=sheet_path, version=version)
                    last_audit = audit
                    print(
                        f"[scheduler] Agent 5 QA v{version}: passed={audit.passed}, "
                        f"score={audit.overall_score}/10 (visual={audit.visual_relevance_score}, "
                        f"sub={audit.subtitle_readability_score}, audio={audit.pacing_and_audio_score}), "
                        f"target={audit.revision_target}, issues={audit.issues}"
                    )

                    if audit.passed:
                        qa_passed = True
                        break

                    if version <= max_revisions:
                        print(f"[scheduler] QA failed -> running revision #{version}/{max_revisions}...")
                        affected = _extract_affected_scenes(audit, valid_scene_ids)
                        need_rescout = (
                            audit.revision_target in ("asset_scout", "both")
                            or (audit.revision_target != "editor" and audit.visual_relevance_score < 6.5)
                        )
                        if need_rescout:
                            target_scenes = affected or valid_scene_ids[1:-1] or valid_scene_ids
                            try:
                                state = rescout_affected_scenes(episode_id, target_scenes)
                                print(f"[scheduler] Agent 6 replaced footage for scenes: {target_scenes}")
                            except Exception as exc:
                                print(f"[scheduler] Re-scout failed for {target_scenes}: {exc}")
                        if audit.revision_target in ("editor", "both") or not need_rescout:
                            for sc_id in affected or valid_scene_ids:
                                scene_offsets[sc_id] = round(scene_offsets.get(sc_id, 0.0) + 2.0, 2)
                            font_ratio = 0.050 if audit.subtitle_readability_score < 8.0 else font_ratio

                if not qa_passed:
                    last_err = f"Topic '{title}' failed QA after {max_revisions} revisions"
                    print(f"[scheduler] {last_err}. Trying next topic...")
                    state.status = "needs_human_review"
                    save_state(state)
                    if title and title != "-":
                        avoid_titles.append(title)
                    _tg_status(f"⚠️ [{brand_name}] Attempt #{total_attempts_done} failed QA ({title}). Retrying with new topic...")
                    continue

                try:
                    state = prepare_episode_thumbnail(episode_id)
                except Exception as exc:
                    print(f"[scheduler] Warning preparing cover thumbnail before final render: {exc}")
                state, final_video, _ = run_final_hd_render(episode_id)
                hd_ok = bool(state.gate0 and state.gate0.passed)
                print(f"[scheduler] Final 1080p render complete: {final_video} (Gate 0 HD passed={hd_ok})")
                if not hd_ok:
                    last_err = f"Gate 0 HD failed on '{title}'"
                    print(f"[scheduler] {last_err}; trying next topic...")
                    if title and title != "-":
                        avoid_titles.append(title)
                    _tg_status(f"⚠️ [{brand_name}] Attempt #{total_attempts_done} failed Gate 0 HD ({title}). Retrying...")
                    continue

                qa_summary = (
                    f"Score {last_audit.overall_score:.1f}/10 "
                    f"(Vis {last_audit.visual_relevance_score:.1f}, Sub {last_audit.subtitle_readability_score:.1f}, "
                    f"Aud {last_audit.pacing_and_audio_score:.1f})"
                    if last_audit
                    else "QA PASSED"
                )

                state = run_agent_7_publish(episode_id, mode=pub_mode_start)
                print(f"[scheduler] Agent 7 complete: {[r.model_dump() for r in (state.agent7.publish_results if state.agent7 else [])]}")

                prod_elapsed = time.monotonic() - run_t0

                # Upload immediately as unlisted draft + Drive folder (NO sleep waiting for target publish time)
                if os.environ.get("AUTO_UPLOAD_GDRIVE", "true").lower() == "true" and can_upload_to_gdrive():
                    state = run_upload(
                        episode_id,
                        allow_youtube=(pub_mode_start != "dry_run"),
                        qa_summary=qa_summary,
                        topic_attempts_used=total_attempts_done,
                        production_seconds=prod_elapsed,
                    )
                    print(
                        f"[scheduler] Uploaded draft to Google Drive & Unlisted YouTube: "
                        f"{state.gdrive_folder_url} | YouTube: {state.youtube_url}"
                    )

                paths = episode_paths(episode_id)
                yt_vid_id = ""
                yt_meta_path = paths["handoffs"] / "youtube_upload.json"
                if yt_meta_path.exists():
                    try:
                        yt_meta = json.loads(yt_meta_path.read_text(encoding="utf-8"))
                        yt_vid_id = str(yt_meta.get("video_id") or "")
                        if not state.youtube_url and yt_meta.get("youtube_url"):
                            state.youtube_url = yt_meta.get("youtube_url")
                    except Exception:
                        pass

                tts_engine = "Edge-TTS"
                tts_meta_path = paths["handoffs"] / "tts_meta.json"
                if tts_meta_path.exists():
                    try:
                        tts_engine = str(json.loads(tts_meta_path.read_text(encoding="utf-8")).get("tts_engine") or "Edge-TTS")
                    except Exception:
                        pass

                winner_cand = (
                    next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
                    if state.agent1 and state.agent2
                    else None
                )
                ref_url = ""
                if state.agent3 and state.agent3.references:
                    ref0 = state.agent3.references[0]
                    ref_url = str(getattr(ref0, "url", None) or ref0 or "")

                pinned_comment = ""
                if state.agent7 and state.agent7.platform_copies:
                    yt_cp = next((c for c in state.agent7.platform_copies if c.platform == "youtube_shorts"), None)
                    if yt_cp and yt_cp.pinned_comment:
                        pinned_comment = yt_cp.pinned_comment.strip()

                gh_run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
                art_name = f"studio-episode-{gh_run_id or episode_id}"
                art_ref = f"{gh_run_id}:{art_name}" if gh_run_id else art_name

                base_payload = {
                    "episode_id": episode_id,
                    "slot": slot,
                    "topic_title": state.agent3.title if state.agent3 else title,
                    "topic_slug": state.agent3.episode_slug if state.agent3 else episode_id,
                    "pillar": winner_cand.discipline if winner_cand else "gadgets",
                    "section_tag": state.render_plan.section_tag if state.render_plan else "EVERYDAY_TECH",
                    "source_url": ref_url,
                    "youtube_video_id": yt_vid_id,
                    "youtube_url": state.youtube_url or "",
                    "drive_url": state.gdrive_folder_url or "",
                    "video_artifact_ref": art_ref,
                    "final_video_path": str(final_video),
                    "thumbnail_path": (state.agent7.thumbnail_path if state.agent7 else "") or "",
                    "footage_tier_summary": _summarize_footage_tiers(state.agent6.assets if state.agent6 else []),
                    "tts_engine": tts_engine,
                    "revision_count": revision_count_override,
                    "qa_summary": qa_summary,
                    "pinned_comment": pinned_comment,
                    "duration_sec": round(state.render_plan.total_duration_sec if state.render_plan else 0.0, 1),
                    "target_publish_iso": target_publish_iso,
                }

                if save_as_backup:
                    entry = upsert_pending_review({**base_payload, "status": "saved_as_backup"})
                    push_backup_video(entry, reason="manual_backup_run")
                    stok = peek_backup_count()
                    send_telegram_message(
                        f"💾 [{brand_name}] Saved straight to Standby Video Bank!\n"
                        f"• Title: {entry.get('topic_title')}\n"
                        f"• Unlisted YT: {entry.get('youtube_url') or '-'}\n"
                        f"• Pro Drive: {entry.get('drive_url') or '-'}\n"
                        f"• Total Video Bank stock: {stok} video(s) ready"
                    )
                    return entry

                entry = upsert_pending_review({**base_payload, "status": "awaiting_review"})
                msg_id = send_interactive_audit_message(entry, video_path=Path(final_video))
                if msg_id:
                    entry = upsert_pending_review({"episode_id": episode_id, "telegram_message_id": msg_id})
                return entry

            except Exception as exc:
                raw_msg = str(exc) or exc.__class__.__name__
                last_err = re.sub(r"([?&](?:key|token)=)[^&\s'\"]+", r"\1***", raw_msg)
                print(f"[scheduler] Attempt #{total_attempts_done} (round {recovery_round}.{topic_attempt}) failed: {last_err}")
                traceback.print_exc()
                if title and title != "-" and title not in avoid_titles:
                    avoid_titles.append(title)
                is_quota_err = ("429" in last_err or "quota" in last_err.lower() or "503" in last_err)
                if is_quota_err:
                    consecutive_quota_errors += 1
                    if consecutive_quota_errors >= 2:
                        break
                    _tg_status(
                        f"⏳ [{brand_name}] Attempt #{total_attempts_done} hit Gemini RPM limit (429). "
                        f"Waiting 75s before retrying 1x..."
                    )
                    time.sleep(75)
                else:
                    consecutive_quota_errors = 0
                    _tg_status(
                        f"⚠️ [{brand_name}] Attempt #{total_attempts_done} failed: {last_err[:160]}\n"
                        f"Retrying with a fresh topic..."
                    )
                continue

        if consecutive_quota_errors >= 2:
            break
        if recovery_round < max_recovery_rounds and (time.monotonic() - run_t0) < max_wall_clock_sec:
            cooldown_s = min(180, 75 * recovery_round)
            print(f"[scheduler] Recovery round {recovery_round} exhausted; cooling down {cooldown_s}s before round {recovery_round + 1}...")
            _tg_status(
                f"🔄 [{brand_name}] Round {recovery_round}/{max_recovery_rounds} failed ({last_err[:140]}). "
                f"Cooling down {cooldown_s}s before round {recovery_round + 1}..."
            )
            time.sleep(cooldown_s)

    # Standby Backup Video Bank Auto-Fallback on production failure
    allow_backup = os.environ.get("ALLOW_BACKUP_FALLBACK", "true").strip().lower() != "false"
    if allow_backup and not save_as_backup:
        b_item = pop_next_backup_video()
        if b_item:
            prom = promote_backup_to_slot(
                b_item,
                slot=slot,
                target_publish_iso=target_publish_iso,
                reason=f"Auto-fallback after production failed ({last_err[:100]})",
            )
            print(f"[scheduler] Activated Standby Video Bank fallback: {prom}")
            return prom

    _tg_status(
        f"❌ [{brand_name}] Production stopped after {total_attempts_done} attempts ({max_recovery_rounds} rounds).\n"
        f"Last error: {last_err[:220]}"
    )
    raise RuntimeError(
        f"All {total_attempts_done} automated production attempts across {max_recovery_rounds} rounds failed: {last_err}"
    )


def _resolve_target_episode(episode_id: Optional[str]) -> dict[str, Any]:
    if episode_id:
        row = get_pending_review(episode_id)
        if row:
            return row
    store = load_pending_reviews().get("episodes") or {}
    cands = [
        dict(v)
        for v in store.values()
        if isinstance(v, dict) and v.get("status") in ("awaiting_review", "revising", "held_by_ai")
    ]
    if not cands:
        raise RuntimeError(f"No actionable pending episode found for episode_id='{episode_id or ''}'")
    cands.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return cands[0]


def handle_approve_episode(episode_id: Optional[str] = None) -> dict[str, Any]:
    """Approve an unlisted draft and schedule/publish it to YouTube."""
    load_dotenv()
    entry = _resolve_target_episode(episode_id)
    ep_id = str(entry["episode_id"])
    if entry.get("status") in ("approved", "approved_by_ai"):
        return {"skipped": True, "episode_id": ep_id, "status": entry.get("status")}

    first_comment = str(entry.get("pinned_comment") or "")
    req_id = None
    try:
        st = load_or_init_state(ep_id)
        req_id = st.request_id
        if st.agent7 and st.agent7.platform_copies:
            yt_cp = next((c for c in st.agent7.platform_copies if c.platform == "youtube_shorts"), None)
            if yt_cp and yt_cp.pinned_comment:
                first_comment = yt_cp.pinned_comment
    except Exception:
        pass

    pub_res = schedule_or_publish_youtube_video(
        video_id=str(entry.get("youtube_video_id") or ""),
        target_publish_iso=str(entry.get("target_publish_iso") or ""),
        first_comment=first_comment,
    )
    mark_review_status(ep_id, "approved", {"approved_at_wib": datetime.now(WIB).isoformat(timespec="seconds")})
    try:
        from src.tools.weekly_growth_auditor import record_published_episode, mark_audience_request_fulfilled

        record_published_episode(
            episode_id=ep_id,
            title=str(entry.get("topic_title") or ep_id),
            discipline=str(entry.get("pillar") or "gadgets"),
            section=str(entry.get("section_tag") or "EVERYDAY_TECH"),
            youtube_url=str(entry.get("youtube_url") or ""),
            gdrive_folder_url=str(entry.get("drive_url") or ""),
            duration_sec=float(entry.get("duration_sec") or 0.0),
            revisions_used=int(entry.get("revision_count") or 0),
        )
        if req_id and entry.get("youtube_url"):
            mark_audience_request_fulfilled(req_id, ep_id, youtube_url=str(entry.get("youtube_url")))
    except Exception:
        pass

    disable_audit_keyboard(entry.get("telegram_message_id"), "✅ Approved by Owner")
    send_telegram_message(
        f"✅ [{get_brand_name()}] Approved & Scheduled!\n"
        f"• Title: {entry.get('topic_title')}\n"
        f"• Target Publish: {entry.get('target_publish_iso')}\n"
        f"• YouTube: {entry.get('youtube_url') or '-'}\n"
        f"• API Status: {'OK' if pub_res.get('ok') else pub_res.get('error', 'Updated')}"
    )
    return {"approved": True, "episode_id": ep_id, "publish_result": pub_res}


def handle_rerender_episode(episode_id: Optional[str] = None, feedback: str = "") -> dict[str, Any]:
    """Delete old unlisted draft, save revision instructions, and re-produce same topic for the slot."""
    load_dotenv()
    entry = _resolve_target_episode(episode_id)
    ep_id = str(entry["episode_id"])

    clean_fb = feedback.strip()
    if clean_fb:
        try:
            from src.tools.telegram_notify import record_owner_feedback

            record_owner_feedback(clean_fb, mode="rerender")
        except Exception:
            pass

    old_vid_id = str(entry.get("youtube_video_id") or "")
    if old_vid_id:
        delete_youtube_video(old_vid_id)

    mark_review_status(ep_id, "superseded", {"revision_feedback": clean_fb})
    disable_audit_keyboard(entry.get("telegram_message_id"), "🔄 Superseded by Revision")

    new_rev = int(entry.get("revision_count") or 0) + 1
    new_entry = run_one_automated_episode(
        topic_hint=str(entry.get("topic_title") or "") or None,
        slot_override=str(entry.get("slot") or "slot_2100"),
        target_publish_iso_override=str(entry.get("target_publish_iso") or ""),
        revision_count_override=new_rev,
    )
    return {"rerendered": True, "superseded_episode_id": ep_id, "new_entry": new_entry}


def handle_reject_and_next_episode(episode_id: Optional[str] = None, reason: str = "owner_rejected") -> dict[str, Any]:
    """Permanently block the rejected topic, delete unlisted YT draft, and generate a brand-new episode."""
    load_dotenv()
    entry = _resolve_target_episode(episode_id)
    ep_id = str(entry["episode_id"])
    title = str(entry.get("topic_title") or "")
    slug = str(entry.get("topic_slug") or ep_id)

    add_rejected_topic(BASE_DIR, title=title or ep_id, slug=slug, reason=reason)
    old_vid_id = str(entry.get("youtube_video_id") or "")
    if old_vid_id:
        delete_youtube_video(old_vid_id)

    mark_review_status(ep_id, "rejected", {"rejected_reason": reason})
    disable_audit_keyboard(entry.get("telegram_message_id"), "❌ Rejected — Next Topic")
    send_telegram_message(
        f"❌ [{get_brand_name()}] Rejected '{title}'. Permanently blocked topic & starting fresh episode..."
    )

    new_entry = run_one_automated_episode(
        slot_override=str(entry.get("slot") or "slot_2100"),
        target_publish_iso_override=str(entry.get("target_publish_iso") or ""),
        revision_count_override=0,
        extra_avoid_titles=[title] if title else None,
    )
    return {"rejected_episode_id": ep_id, "new_entry": new_entry}


def promote_backup_to_slot(
    backup_item: dict[str, Any],
    *,
    slot: Optional[str] = None,
    target_publish_iso: Optional[str] = None,
    reason: str = "Standby Video Bank promotion",
) -> dict[str, Any]:
    """Promote 1 unlisted standby Video Bank item into the active review slot and send interactive Telegram card."""
    resolved_slot = _resolve_slot(slot)
    pub_iso = target_publish_iso or compute_target_publish_iso(resolved_slot)
    ep_id = str(backup_item.get("episode_id") or "")
    entry = upsert_pending_review({
        **backup_item,
        "episode_id": ep_id,
        "slot": resolved_slot,
        "target_publish_iso": pub_iso,
        "revision_count": 0,
        "reminder_sent": False,
        "status": "awaiting_review",
    })
    stok = peek_backup_count()
    send_telegram_message(
        f"🆘💾 [{get_brand_name()}] STANDBY VIDEO BANK ACTIVATED\n"
        f"• Reason: {reason}\n"
        f"• Title: {entry.get('topic_title')}\n"
        f"• Remaining Video Bank stock: {stok} video(s)"
    )
    vp = Path(str(entry.get("final_video_path") or "")) if entry.get("final_video_path") else None
    msg_id = send_interactive_audit_message(entry, video_path=vp if (vp and vp.exists()) else None)
    if msg_id:
        entry = upsert_pending_review({"episode_id": ep_id, "telegram_message_id": msg_id})
    return entry


def handle_save_to_backup(episode_id: Optional[str] = None) -> dict[str, Any]:
    """Move an active awaiting_review draft into Standby Video Bank (kept unlisted & shielded from AI auto-publish)."""
    load_dotenv()
    entry = _resolve_target_episode(episode_id)
    ep_id = str(entry["episode_id"])
    if entry.get("status") in ("approved", "approved_by_ai"):
        disable_audit_keyboard(entry.get("telegram_message_id"), "✅ Already Approved")
        raise RuntimeError(f"Episode '{ep_id}' is already approved and cannot move to Video Bank.")

    # Preserve pinned_comment from local state if present
    if not entry.get("pinned_comment"):
        try:
            st = load_or_init_state(ep_id)
            if st.agent7 and st.agent7.platform_copies:
                yt_cp = next((c for c in st.agent7.platform_copies if c.platform == "youtube_shorts"), None)
                if yt_cp and yt_cp.pinned_comment:
                    entry["pinned_comment"] = yt_cp.pinned_comment.strip()
        except Exception:
            pass

    mark_review_status(ep_id, "saved_as_backup", {"saved_to_bank_at_wib": datetime.now(WIB).isoformat(timespec="seconds")})
    push_backup_video(entry, reason="telegram_save_backup")
    disable_audit_keyboard(entry.get("telegram_message_id"), "💾 Saved to Video Bank")
    stok = peek_backup_count()
    send_telegram_message(
        f"💾 [{get_brand_name()}] Moved '{entry.get('topic_title')}' into Standby Video Bank!\n"
        f"• YouTube stays Unlisted ({entry.get('youtube_url') or '-'})\n"
        f"• Protected from automatic 20:45 / 22:30 AI publish\n"
        f"• Standby Video Bank stock: {stok} video(s)"
    )
    return {"saved_to_backup": True, "episode_id": ep_id, "backup_count": stok}


def handle_reject_and_use_backup(episode_id: Optional[str] = None, reason: str = "owner_rejected_use_backup") -> dict[str, Any]:
    """Blacklist rejected topic, delete unlisted YT draft, and promote 1 video from Standby Video Bank (or produce new if empty)."""
    load_dotenv()
    entry = _resolve_target_episode(episode_id)
    ep_id = str(entry["episode_id"])
    title = str(entry.get("topic_title") or "")
    slug = str(entry.get("topic_slug") or ep_id)
    slot = str(entry.get("slot") or "slot_2100")
    target_iso = str(entry.get("target_publish_iso") or compute_target_publish_iso(slot))

    add_rejected_topic(BASE_DIR, title=title or ep_id, slug=slug, reason=reason)
    old_vid_id = str(entry.get("youtube_video_id") or "")
    if old_vid_id:
        delete_youtube_video(old_vid_id)

    mark_review_status(ep_id, "rejected", {"rejected_reason": reason})
    disable_audit_keyboard(entry.get("telegram_message_id"), "❌ Rejected — Swapped")

    b_item = pop_next_backup_video()
    if not b_item:
        send_telegram_message(
            f"⚠️ [{get_brand_name()}] Video Bank is currently empty — producing a fresh replacement episode for '{slot}'..."
        )
        new_entry = run_one_automated_episode(
            slot_override=slot,
            target_publish_iso_override=target_iso,
            revision_count_override=0,
            extra_avoid_titles=[title] if title else None,
        )
        return {"rejected_episode_id": ep_id, "used_backup": False, "new_entry": new_entry}

    prom = promote_backup_to_slot(
        b_item,
        slot=slot,
        target_publish_iso=target_iso,
        reason=f"Replacement for rejected topic '{title}'",
    )
    return {"rejected_episode_id": ep_id, "used_backup": True, "promoted_entry": prom}


def _run_daemon_loop() -> None:
    load_dotenv()
    default_slots = ",".join(get_daily_slots_wib())
    times_raw = os.environ.get("SCHEDULE_TIMES_WIB", default_slots)
    target_times = {t.strip() for t in times_raw.split(",") if t.strip()}
    last_ran_key = ""
    print(f"[scheduler] {get_brand_name()} 24/7 active. Daily schedule (WIB): {sorted(target_times)}")

    while True:
        now = datetime.now(WIB)
        hhmm = now.strftime("%H:%M")
        run_key = now.strftime("%Y-%m-%d_%H:%M")
        if hhmm in target_times and run_key != last_ran_key:
            last_ran_key = run_key
            try:
                run_one_automated_episode()
            except Exception:
                traceback.print_exc()
        time.sleep(20)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Bill_Explains Studio Scheduler & Dispatch Handlers")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run")
    p_run.add_argument("--slot", default=None)
    p_run.add_argument("--topic-hint", default=None)
    p_run.add_argument("--save-as-backup", action="store_true")

    p_app = sub.add_parser("approve")
    p_app.add_argument("--episode-id", default=None)

    p_sb = sub.add_parser("save-backup")
    p_sb.add_argument("--episode-id", default=None)

    p_rev = sub.add_parser("rerender")
    p_rev.add_argument("--episode-id", default=None)
    p_rev.add_argument("--feedback", default="")

    p_rej = sub.add_parser("reject-next")
    p_rej.add_argument("--episode-id", default=None)
    p_rej.add_argument("--reason", default="owner_rejected")

    p_rb = sub.add_parser("reject-backup")
    p_rb.add_argument("--episode-id", default=None)
    p_rb.add_argument("--reason", default="owner_rejected_use_backup")

    sub.add_parser("daemon")

    args = parser.parse_args()
    if args.cmd == "run":
        out = run_one_automated_episode(
            topic_hint=args.topic_hint,
            slot_override=args.slot,
            save_as_backup_override=True if args.save_as_backup else None,
        )
        print(json.dumps(out or {}, ensure_ascii=False, indent=2))
    elif args.cmd == "approve":
        print(json.dumps(handle_approve_episode(args.episode_id), ensure_ascii=False, indent=2))
    elif args.cmd == "save-backup":
        print(json.dumps(handle_save_to_backup(args.episode_id), ensure_ascii=False, indent=2))
    elif args.cmd == "rerender":
        print(json.dumps(handle_rerender_episode(args.episode_id, args.feedback), ensure_ascii=False, indent=2))
    elif args.cmd == "reject-next":
        print(json.dumps(handle_reject_and_next_episode(args.episode_id, args.reason), ensure_ascii=False, indent=2))
    elif args.cmd == "reject-backup":
        print(json.dumps(handle_reject_and_use_backup(args.episode_id, args.reason), ensure_ascii=False, indent=2))
    else:
        _run_daemon_loop()


if __name__ == "__main__":
    main()
