"""24/7 Automated Scheduler for Everyday Sci-Tech International Studio.

Runs the full 8-agent pipeline automatically on a 2x/day global prime-time schedule
(default 04:00 & 21:00 WIB -> 16:00 EST & 09:00 EST, offset from Kimiabill to avoid API rate limits).
"""
from __future__ import annotations

import os
import re
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from src.brand_config import get_brand_name, get_daily_slots_wib
from src.pipeline import (
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
from src.tools.gdrive_sync import can_upload_to_gdrive

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


def _tg_status(message: str) -> None:
    """Best-effort live Telegram status update during automated production (never raises)."""
    try:
        from src.tools.telegram_notify import send_telegram_message, telegram_enabled

        if telegram_enabled():
            send_telegram_message(message)
    except Exception:
        pass


def run_one_automated_episode() -> None:
    load_dotenv()
    brand_name = get_brand_name()
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
        return

    guardrails = brand_cfg.get("guardrails", {})
    max_revisions = int(guardrails.get("max_revisions", 4))
    max_topic_attempts = int(guardrails.get("max_topic_attempts", 3))
    avoid_titles: list[str] = []
    try:
        from src.tools.weekly_growth_auditor import HISTORY_PATH
        import json as _json

        if HISTORY_PATH.exists():
            hist_items = _json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            for row in hist_items[-16:]:
                t = (row.get("title") or "").strip()
                if t and t not in avoid_titles:
                    avoid_titles.append(t)
            if os.environ.get("SKIP_IF_SLOT_PUBLISHED", "").lower() == "true":
                now_wib = datetime.now(WIB)
                target_pub_env = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
                slot_late = (
                    _is_late_slot(int(target_pub_env.split(":", 1)[0]))
                    if ":" in target_pub_env
                    else _is_late_slot(now_wib.hour)
                )
                for row in reversed(hist_items):
                    pub_at = row.get("published_at_wib") or ""
                    has_slot_proof = (
                        bool(row.get("youtube_url"))
                        if row.get("publish_mode", "public") != "dry_run"
                        else bool(row.get("gdrive_folder_url"))
                    )
                    if (
                        pub_at.startswith(now_wib.strftime("%Y-%m-%d"))
                        and row.get("publish_mode", "public") == "public"
                        and has_slot_proof
                    ):
                        try:
                            pub_hour = datetime.fromisoformat(pub_at).hour
                            if _is_late_slot(pub_hour) == slot_late:
                                print(
                                    f"[scheduler] Slot already published today "
                                    f"({row.get('youtube_url') or row.get('gdrive_folder_url')}) — skipping duplicate cron."
                                )
                                return
                        except Exception:
                            pass
    except Exception:
        pass

    # Check if Agent 8 queued a viewer comment request to fulfill on weekday slots
    pending_req: dict | None = None
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
    pub_mode_start = os.environ.get("PUBLISH_MODE", "public").strip().lower()
    target_pub_label = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip() or "langsung tayang"
    _tg_status(
        f"🎬 [{brand_name}] Mulai produksi video otomatis!\n"
        f"• Target tayang: {target_pub_label} WIB\n"
        f"• Mode: {pub_mode_start}\n"
        f"• Auto-retry aktif hingga {max_recovery_rounds} ronde ({max_recovery_rounds * max_topic_attempts} percobaan)"
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
            episode_id = f"ep_{now.strftime('%Y%m%d_%H%M')}{ep_suffix}"
            print(
                f"[scheduler] [{brand_name}] Starting automated production "
                f"(round {recovery_round}/{max_recovery_rounds}, attempt {topic_attempt}/{max_topic_attempts}): {episode_id}"
            )

            title = "-"
            try:
                use_req = pending_req if (recovery_round == 1 and topic_attempt == 1) else None
                state = run_agent_1_to_3(
                    episode_id,
                    topic_hint=use_req.get("topic_title") if use_req else None,
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
                    f"📝 [{brand_name}] Skrip siap (Ronde {recovery_round}, Percobaan #{topic_attempt}):\n"
                    f"\"{title}\"\n"
                    f"⏳ Sedang cari footage, TTS, animasi & QA render..."
                )

                state = run_agent_6_scout(episode_id)
                print(f"[scheduler] Agent 6 complete: {len(state.agent6.assets) if state.agent6 else 0} real-footage clips")

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
                            or audit.visual_relevance_score < 8.0
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
                    last_err = f"Topik '{title}' gagal QA setelah {max_revisions} revisi"
                    print(f"[scheduler] {last_err}. NOT uploading; trying a new topic...")
                    state.status = "needs_human_review"
                    save_state(state)
                    if title and title != "-":
                        avoid_titles.append(title)
                    _tg_status(f"⚠️ [{brand_name}] Percobaan #{total_attempts_done} gagal QA ({title}). Otomatis mengulang dengan topik baru...")
                    continue

                try:
                    state = prepare_episode_thumbnail(episode_id)
                except Exception as exc:
                    print(f"[scheduler] Warning preparing cover thumbnail before final render: {exc}")
                state, final_video, _ = run_final_hd_render(episode_id)
                hd_ok = bool(state.gate0 and state.gate0.passed)
                print(f"[scheduler] Final 1080p render complete: {final_video} (Gate 0 HD passed={hd_ok})")
                if not hd_ok:
                    last_err = f"Gate 0 HD gagal pada '{title}'"
                    print(f"[scheduler] {last_err}; trying next topic...")
                    if title and title != "-":
                        avoid_titles.append(title)
                    _tg_status(f"⚠️ [{brand_name}] Percobaan #{total_attempts_done} gagal Gate 0 HD ({title}). Otomatis mengulang...")
                    continue

                qa_summary = (
                    f"QA PASSED | Score: {last_audit.overall_score}/10 | "
                    f"Visual: {last_audit.visual_relevance_score} | Subtitles: {last_audit.subtitle_readability_score} | "
                    f"Audio: {last_audit.pacing_and_audio_score} | Revisions: {state.revision_count}x"
                    if last_audit
                    else "QA PASSED"
                )

                state = run_agent_7_publish(episode_id)
                print(f"[scheduler] Agent 7 complete: {[r.model_dump() for r in (state.agent7.publish_results if state.agent7 else [])]}")

                prod_elapsed = time.monotonic() - run_t0

                target_pub = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
                if target_pub and datetime.now(WIB).weekday() in (5, 6):
                    try:
                        from src.tools.weekly_growth_auditor import load_latest_agent_directives

                        rec_hours = load_latest_agent_directives().get("recommended_weekend_hours_wib") or []
                        is_late = _is_late_slot(int(target_pub.split(":", 1)[0]))
                        picked = [h for h in rec_hours if ":" in h and _is_late_slot(int(h.split(":", 1)[0])) == is_late]
                        if picked:
                            target_pub = picked[0].strip()
                            print(f"[scheduler] Weekend TECH_RADAR using Agent 8 optimal hour: {target_pub} WIB")
                    except Exception:
                        pass
                if target_pub and ":" in target_pub:
                    try:
                        th, tm = (int(x) for x in target_pub.split(":", 1))
                        now_wib = datetime.now(WIB)
                        target_dt = now_wib.replace(hour=th, minute=tm, second=0, microsecond=0)
                        wait_sec = (target_dt - now_wib).total_seconds() - 90.0
                        if 0 < wait_sec <= 9000:
                            print(
                                f"[scheduler] Video QA passed! Waiting for publish window {target_pub} WIB "
                                f"({int(wait_sec // 60)}m {int(wait_sec % 60)}s left)..."
                            )
                            _tg_status(
                                f"⏳ [{brand_name}] Video \"{title}\" lolos QA ({last_audit.overall_score if last_audit else 9.0}/10)! "
                                f"Menunggu jendela tayang {target_pub} WIB ({int(wait_sec // 60)} menit lagi)..."
                            )
                            time.sleep(wait_sec)
                    except Exception as exc:
                        print(f"[scheduler] Ignoring TARGET_PUBLISH_TIME_WIB='{target_pub}': {exc}")

                if os.environ.get("AUTO_UPLOAD_GDRIVE", "true").lower() == "true":
                    if can_upload_to_gdrive():
                        state = run_upload(
                            episode_id,
                            allow_youtube=True,
                            qa_summary=qa_summary,
                            topic_attempts_used=total_attempts_done,
                            production_seconds=prod_elapsed,
                        )
                        print(f"[scheduler] Uploaded to Google Drive & YouTube Shorts: {state.gdrive_folder_url} | YouTube: {state.youtube_url}")
                        pub_mode_now = os.environ.get("PUBLISH_MODE", "public").strip().lower()
                        if pub_mode_now in ("public", "draft") and not state.youtube_url:
                            raise RuntimeError(
                                f"Video '{episode_id}' uploaded to Drive ({state.gdrive_folder_url}), "
                                "but YouTube auto-upload FAILED (youtube_url empty)."
                            )
                        if (
                            state.request_id
                            and pub_mode_now == "public"
                            and state.status == "uploaded"
                            and state.youtube_url
                        ):
                            try:
                                from src.tools.weekly_growth_auditor import mark_audience_request_fulfilled

                                mark_audience_request_fulfilled(
                                    state.request_id,
                                    episode_id,
                                    youtube_url=state.youtube_url,
                                )
                                print(f"[scheduler] Viewer request '{state.request_id}' ({state.requested_by}) marked fulfilled!")
                            except Exception:
                                pass
                    else:
                        print("[scheduler] Skipping Google Drive upload (GDRIVE_WEBHOOK_URL missing and 'gdrive' CLI not installed).")
                return
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
                        _tg_status(
                            f"🛑 [{brand_name}] Kuota Gemini API habis (429/503 beruntun setelah jeda RPM).\n"
                            f"Produksi dihentikan sementara agar kuota pulih pada jadwal berikutnya."
                        )
                        raise RuntimeError(f"Gemini API quota exhausted across consecutive attempts: {last_err}") from exc
                    _tg_status(
                        f"⏳ [{brand_name}] Percobaan #{total_attempts_done} kena limit Gemini (429). "
                        f"Menunggu 75 detik agar kuota RPM pulih sebelum mencoba ulang 1x lagi..."
                    )
                    time.sleep(75)
                else:
                    consecutive_quota_errors = 0
                    _tg_status(
                        f"⚠️ [{brand_name}] Percobaan #{total_attempts_done} gagal: {last_err[:160]}\n"
                        f"Otomatis mengulang dengan topik baru..."
                    )
                continue

        if recovery_round < max_recovery_rounds and (time.monotonic() - run_t0) < max_wall_clock_sec:
            cooldown_s = min(180, 75 * recovery_round)
            print(f"[scheduler] Recovery round {recovery_round} exhausted; cooling down {cooldown_s}s before round {recovery_round + 1}...")
            _tg_status(
                f"🔄 [{brand_name}] Ronde {recovery_round}/{max_recovery_rounds} gagal ({last_err[:140]}). "
                f"Jeda {cooldown_s} detik lalu lanjut ronde {recovery_round + 1}..."
            )
            time.sleep(cooldown_s)

    _tg_status(
        f"❌ [{brand_name}] Produksi terhenti setelah {total_attempts_done} percobaan ({max_recovery_rounds} ronde).\n"
        f"Error terakhir: {last_err[:220]}"
    )
    raise RuntimeError(
        f"All {total_attempts_done} automated production attempts across {max_recovery_rounds} rounds failed: {last_err}"
    )


def main() -> None:
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


if __name__ == "__main__":
    main()
