"""AI Chief Editor Blind Multimodal MP4 Auditor + Reminder Fallback (`src/chief_editor_audit.py`).

Runs when:
- Owner clicks `🤖 Delegate to AI` (`telegram_ai_audit` dispatch / `audit --episode-id ...`)
- Scheduled deadline arrives without manual action (`20:45 WIB` Slot 1 / `22:30 WIB` Slot 2)

Primary path uploads the rendered MP4 to the Gemini File API so the model inspects BOTH
video frames and native audio speech/prosody in one blind pass, with guaranteed `finally:` cleanup.
Fallback path extracts 9 evenly spaced keyframes via FFmpeg + Vision/OpenRouter audit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, Optional

import requests
from pydantic import BaseModel, Field

from src.brand_config import BASE_DIR, get_brand_name
from src.pipeline import episode_paths, load_dotenv, load_or_init_state
from src.tools.pending_review import (
    find_episodes_by_status,
    get_pending_review,
    mark_review_status,
    upsert_pending_review,
)
from src.tools.social_publisher import schedule_or_publish_youtube_video
from src.tools.telegram_notify import disable_audit_keyboard, send_telegram_message
from src.tools.weekly_growth_auditor import record_published_episode


class ChiefEditorChecks(BaseModel):
    hook_visual_match: Literal["PASS", "FAIL"]
    tts_pronunciation_and_flow: Literal["PASS", "FAIL"]
    english_only_and_handle: Literal["PASS", "FAIL"]
    science_fact_accuracy: Literal["PASS", "FAIL"]
    animation_diversity_and_clean_text: Literal["PASS", "FAIL"]
    zero_static_outro: Literal["PASS", "FAIL"]


class ChiefEditorVerdict(BaseModel):
    decision: Literal["APPROVE", "REVISE", "HOLD"]
    overall_score: float = Field(ge=0.0, le=10.0)
    fatal_flaws: list[str] = Field(default_factory=list)
    checks: ChiefEditorChecks
    revision_instruction: str = ""


def _build_audit_prompt(entry: dict[str, Any]) -> str:
    return (
        "You are the AI Chief Editor conducting a STRICT BLIND AUDIT of this finished 9:16 YouTube Short MP4 "
        "before public release on Bill_Explains (@Bill_explains12).\n"
        f"Expected Topic: {entry.get('topic_title') or '-'}\n"
        f"Slot: {entry.get('slot') or 'slot_2100'}\n\n"
        "Watch AND listen to the entire video critically. Evaluate these 6 mandatory checks:\n"
        "1. hook_visual_match: Does Shot #1 match the spoken hook smoothly, full-screen 9:16 (no black bars or weird tiny boxes), "
        "and avoid off-topic stock clips?\n"
        "2. tts_pronunciation_and_flow: Is speech natural American English with crisp pacing (no literal 'underscore', "
        "no weird robotic gaps, scientific acronyms/terms pronounced cleanly)?\n"
        "3. english_only_and_handle: Is EVERY visible label and subtitle pure English (ZERO Indonesian words like SIMULASI, "
        "EFEK, HASIL, MOLEKUL, REAKSI), displaying '@Bill_explains12' (never '@bill_explains'), and zero 'Follow' on YouTube CTA?\n"
        "4. science_fact_accuracy: Do narration, on-screen labels, and diagrams agree scientifically without contradiction "
        "(e.g., never mislabeling non-bonding surfaces as REACTION / BOND FORMED)?\n"
        "5. animation_diversity_and_clean_text: Do consecutive steps use distinct visual templates cleanly without text clipping?\n"
        "6. zero_static_outro: Does the ending keep active visual + compact Subscribe badge with ZERO static 'THANK YOU FOR WATCHING' or static 'SOURCES' card?\n\n"
        "Decision rules:\n"
        "- APPROVE: ONLY if overall_score >= 8.0 AND ALL 6 checks are PASS AND fatal_flaws is empty.\n"
        "- REVISE: Fixable issues exist; write exact concrete `revision_instruction` (1-2 sentences).\n"
        "- HOLD: Severe/unfixable flaw.\n\n"
        "Return ONLY valid JSON matching:\n"
        '{"decision":"APPROVE"|"REVISE"|"HOLD","overall_score":0.0,"fatal_flaws":[],"checks":{'
        '"hook_visual_match":"PASS"|"FAIL","tts_pronunciation_and_flow":"PASS"|"FAIL",'
        '"english_only_and_handle":"PASS"|"FAIL","science_fact_accuracy":"PASS"|"FAIL",'
        '"animation_diversity_and_clean_text":"PASS"|"FAIL","zero_static_outro":"PASS"|"FAIL"},'
        '"revision_instruction":""}'
    )


def _strip_json_fences(raw: str) -> str:
    txt = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt)
    if m:
        return m.group(1).strip()
    start = txt.find("{")
    end = txt.rfind("}")
    if start != -1 and end != -1 and end > start:
        return txt[start : end + 1]
    return txt


def resolve_episode_mp4(episode_id: str, entry: dict[str, Any]) -> tuple[Optional[Path], Optional[Path]]:
    """Locate MP4 locally or download from GitHub Actions artifact for cross-run AI Chief Editor audits.

    Returns `(video_path, temp_dir_to_cleanup)`.
    """
    candidates: list[Path] = []
    p_str = str(entry.get("final_video_path") or "").strip()
    if p_str:
        candidates.append(Path(p_str))
    ep_dir = episode_paths(episode_id)["ep_dir"]
    candidates.extend(sorted(ep_dir.glob("FINAL_*.mp4")))
    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 10_000:
            return cand, None

    # Attempt downloading uploaded workflow artifact in GitHub Actions
    art_ref = str(entry.get("video_artifact_ref") or "").strip()
    run_id = ""
    art_name = f"studio-episode-{episode_id}"
    if ":" in art_ref:
        run_id, art_name = art_ref.split(":", 1)
    elif art_ref:
        art_name = art_ref

    if shutil.which("gh") and (run_id or os.environ.get("GITHUB_REPOSITORY")):
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"audit_{episode_id}_"))
        try:
            if not run_id:
                list_cmd = [
                    "gh", "run", "list", "--workflow", "daily_studio.yml",
                    "--limit", "8", "--json", "databaseId,conclusion",
                ]
                out = subprocess.check_output(list_cmd, cwd=BASE_DIR, text=True, timeout=25)
                runs = json.loads(out)
                for r in runs:
                    rid = str(r.get("databaseId") or "")
                    if not rid:
                        continue
                    dl = subprocess.run(
                        ["gh", "run", "download", rid, "-n", art_name, "-D", str(tmp_dir)],
                        cwd=BASE_DIR,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if dl.returncode == 0:
                        mp4s = list(tmp_dir.rglob("FINAL_*.mp4"))
                        if mp4s:
                            return mp4s[0], tmp_dir
            else:
                dl = subprocess.run(
                    ["gh", "run", "download", run_id, "-n", art_name, "-D", str(tmp_dir)],
                    cwd=BASE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if dl.returncode == 0:
                    mp4s = list(tmp_dir.rglob("FINAL_*.mp4"))
                    if mp4s:
                        return mp4s[0], tmp_dir
        except Exception as exc:
            print(f"[chief_editor] Artifact fetch failed: {exc}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return None, None


def _upload_and_audit_gemini_file_api(video_path: Path, entry: dict[str, Any]) -> ChiefEditorVerdict:
    """Upload MP4 via Gemini File API, poll ACTIVE, inspect video+audio natively, and ALWAYS delete."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing")

    preferred = os.environ.get("CHIEF_EDITOR_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
    model_candidates = [m for m in (preferred, "gemini-2.5-flash", "gemini-2.0-flash") if m]
    seen_models: list[str] = []
    models = [m for m in model_candidates if not (m in seen_models or seen_models.append(m))]

    size_bytes = video_path.stat().st_size
    init_resp = requests.post(
        f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size_bytes),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": video_path.name}},
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["X-Goog-Upload-URL"]

    file_name = ""
    try:
        with video_path.open("rb") as fh:
            up_resp = requests.post(
                upload_url,
                headers={
                    "Content-Length": str(size_bytes),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                data=fh,
                timeout=180,
            )
        up_resp.raise_for_status()
        file_obj = up_resp.json().get("file") or up_resp.json()
        file_name = str(file_obj.get("name") or "")
        file_uri = str(file_obj.get("uri") or "")
        if not file_name or not file_uri:
            raise RuntimeError(f"Gemini File API returned invalid file metadata: {up_resp.text[:200]}")

        # Poll until ACTIVE
        deadline = time.monotonic() + 90.0
        state_val = str(file_obj.get("state") or "PROCESSING")
        while state_val == "PROCESSING" and time.monotonic() < deadline:
            time.sleep(3.0)
            st_resp = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}",
                timeout=20,
            )
            st_resp.raise_for_status()
            state_val = str(st_resp.json().get("state") or "")
        if state_val != "ACTIVE":
            raise RuntimeError(f"Gemini File API file state={state_val} (expected ACTIVE)")

        prompt = _build_audit_prompt(entry)
        last_exc: Optional[Exception] = None
        for model in models:
            for retry in range(3):
                try:
                    gen_resp = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                        headers={"Content-Type": "application/json"},
                        json={
                            "contents": [
                                {
                                    "role": "user",
                                    "parts": [
                                        {"fileData": {"mimeType": "video/mp4", "fileUri": file_uri}},
                                        {"text": prompt},
                                    ],
                                }
                            ],
                            "generationConfig": {
                                "temperature": 0.15,
                                "responseMimeType": "application/json",
                            },
                        },
                        timeout=120,
                    )
                    if gen_resp.status_code == 429:
                        time.sleep(min(45, 8 * (2**retry)))
                        continue
                    if gen_resp.status_code == 404:
                        last_exc = RuntimeError(f"Model {model} not available (404)")
                        break
                    gen_resp.raise_for_status()
                    cands = gen_resp.json().get("candidates") or []
                    text_out = (
                        ((cands[0].get("content") or {}).get("parts") or [{}])[0].get("text") or ""
                    )
                    raw_json = _strip_json_fences(text_out)
                    return ChiefEditorVerdict.model_validate_json(raw_json)
                except Exception as exc:
                    last_exc = exc
                    time.sleep(2.0)
        raise RuntimeError(f"Gemini File API audit failed across models: {last_exc}")
    finally:
        if file_name:
            try:
                requests.delete(
                    f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}",
                    timeout=20,
                )
                print(f"[chief_editor] Deleted Gemini File API asset: {file_name}")
            except Exception as del_exc:
                print(f"[chief_editor] Warning: failed to delete {file_name}: {del_exc}")


def _fallback_keyframe_audit(video_path: Optional[Path], entry: dict[str, Any]) -> ChiefEditorVerdict:
    """Fallback multimodal audit using 9 keyframes + Vision LLM when File API is unavailable."""
    from src.providers import call_structured_agent

    frames: list[Path] = []
    tmp_dir: Optional[Path] = None
    if video_path and video_path.exists():
        tmp_dir = Path(tempfile.mkdtemp(prefix="chief_frames_"))
        try:
            dur_raw = subprocess.check_output(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
                ],
                text=True,
                timeout=15,
            ).strip()
            dur = max(4.0, float(dur_raw))
        except Exception:
            dur = 34.0
        for idx in range(9):
            ts = dur * ((idx + 0.5) / 9.0)
            out_jpg = tmp_dir / f"kf_{idx + 1}.jpg"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                    "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", str(out_jpg),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            if out_jpg.exists():
                frames.append(out_jpg)

    try:
        prompt = _build_audit_prompt(entry)
        verdict = call_structured_agent(
            provider="gemini_api",
            model=os.environ.get("CHIEF_EDITOR_FALLBACK_MODEL", "gemini-2.5-flash"),
            system_prompt=prompt,
            user_payload={
                "note": "Keyframes extracted from final MP4 (File API fallback mode).",
                "episode_summary": {
                    "topic_title": entry.get("topic_title"),
                    "tts_engine": entry.get("tts_engine"),
                    "footage_tier_summary": entry.get("footage_tier_summary"),
                    "qa_summary": entry.get("qa_summary"),
                    "duration_sec": entry.get("duration_sec"),
                },
            },
            response_model=ChiefEditorVerdict,
            image_paths=frames[:6] if frames else None,
        )
        return verdict
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _normalize_verdict(verdict: ChiefEditorVerdict, revision_count: int) -> ChiefEditorVerdict:
    checks = verdict.checks
    all_pass = all(
        v == "PASS"
        for v in (
            checks.hook_visual_match,
            checks.tts_pronunciation_and_flow,
            checks.english_only_and_handle,
            checks.science_fact_accuracy,
            checks.animation_diversity_and_clean_text,
            checks.zero_static_outro,
        )
    )
    if verdict.decision == "APPROVE" and (not all_pass or verdict.overall_score < 8.0 or verdict.fatal_flaws):
        verdict.decision = "REVISE" if revision_count < 2 else "HOLD"
    if verdict.decision == "REVISE" and revision_count >= 2:
        verdict.decision = "HOLD"
    return verdict


def run_chief_editor_audit_episode(episode_id: str) -> dict[str, Any]:
    """Run blind MP4 audit on `episode_id` and execute APPROVE / REVISE / HOLD transition."""
    load_dotenv()
    entry = get_pending_review(episode_id)
    if entry is None:
        raise RuntimeError(f"Episode '{episode_id}' not found in data/pending_review.json")

    if entry.get("status") in ("approved", "approved_by_ai", "rejected"):
        return {"skipped": True, "reason": f"already_{entry.get('status')}", "episode_id": episode_id}

    rev_count = int(entry.get("revision_count") or 0)
    video_path, cleanup_dir = resolve_episode_mp4(episode_id, entry)
    try:
        audit_mode = "gemini_file_api"
        try:
            if video_path and video_path.exists():
                verdict = _upload_and_audit_gemini_file_api(video_path, entry)
            else:
                raise RuntimeError("MP4 not found locally or in GitHub Artifacts")
        except Exception as exc:
            print(f"[chief_editor] Primary File API audit failed ({exc}); running keyframe fallback.")
            audit_mode = "keyframe_fallback"
            verdict = _fallback_keyframe_audit(video_path, entry)
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)

    verdict = _normalize_verdict(verdict, rev_count)
    verdict_dict = {**verdict.model_dump(), "audit_mode": audit_mode}
    brand = get_brand_name()

    if verdict.decision == "APPROVE":
        # Lookup pinned comment from episode state if local
        first_comment = ""
        try:
            st = load_or_init_state(episode_id)
            if st.agent7 and st.agent7.platform_copies:
                yt_cp = next((c for c in st.agent7.platform_copies if c.platform == "youtube_shorts"), None)
                if yt_cp:
                    first_comment = yt_cp.pinned_comment
        except Exception:
            pass

        pub_res = schedule_or_publish_youtube_video(
            video_id=str(entry.get("youtube_video_id") or ""),
            target_publish_iso=str(entry.get("target_publish_iso") or ""),
            first_comment=first_comment,
        )
        mark_review_status(episode_id, "approved_by_ai", {"ai_audit_result": verdict_dict})
        try:
            record_published_episode(
                episode_id=episode_id,
                title=str(entry.get("topic_title") or episode_id),
                discipline=str(entry.get("pillar") or "gadgets"),
                section=str(entry.get("section_tag") or "EVERYDAY_TECH"),
                youtube_url=str(entry.get("youtube_url") or ""),
                gdrive_folder_url=str(entry.get("drive_url") or ""),
                qa_score=verdict.overall_score,
                duration_sec=float(entry.get("duration_sec") or 0.0),
                hook_line="",
                revisions_used=rev_count,
            )
        except Exception:
            pass
        disable_audit_keyboard(entry.get("telegram_message_id"), "🤖 Approved by AI Chief Editor")
        send_telegram_message(
            f"🤖✅ [{brand}] AI Chief Editor APPROVED ({verdict.overall_score:.1f}/10, {audit_mode}):\n"
            f"• Title: {entry.get('topic_title')}\n"
            f"• Target Publish: {entry.get('target_publish_iso')}\n"
            f"• YouTube: {entry.get('youtube_url') or '-'}\n"
            f"• Status: {'Scheduled/Public' if pub_res.get('ok') else pub_res.get('error', 'Updated')}"
        )
        return {"decision": "APPROVE", "verdict": verdict_dict, "publish_result": pub_res}

    if verdict.decision == "REVISE" and rev_count < 2:
        from src.scheduler import handle_rerender_episode

        mark_review_status(episode_id, "revising", {"ai_audit_result": verdict_dict})
        send_telegram_message(
            f"🤖🔄 [{brand}] AI Chief Editor requested REVISION ({verdict.overall_score:.1f}/10):\n"
            f"• Title: {entry.get('topic_title')}\n"
            f"• Instruction: {verdict.revision_instruction or '; '.join(verdict.fatal_flaws[:2])}"
        )
        res = handle_rerender_episode(
            episode_id=episode_id,
            feedback=verdict.revision_instruction or "; ".join(verdict.fatal_flaws),
        )
        return {"decision": "REVISE", "verdict": verdict_dict, "rerender": res}

    # HOLD
    mark_review_status(episode_id, "held_by_ai", {"ai_audit_result": verdict_dict})
    disable_audit_keyboard(entry.get("telegram_message_id"), "⚠️ Held by AI (Unlisted)")
    flaws_txt = "\n".join(f"- {f}" for f in verdict.fatal_flaws[:5]) or "- Score below 8.0 or check failed"
    send_telegram_message(
        f"🤖⚠️ [{brand}] AI Chief Editor HELD episode (kept unlisted):\n"
        f"• Episode: {episode_id} — {entry.get('topic_title')}\n"
        f"• Score: {verdict.overall_score:.1f}/10 ({audit_mode})\n"
        f"• Flaws:\n{flaws_txt}\n"
        f"Reply or review manually before publishing."
    )
    return {"decision": "HOLD", "verdict": verdict_dict}


def send_review_reminders(slot: Optional[str] = None) -> list[str]:
    """Send 15-minute warning if an awaiting_review episode has not been touched yet."""
    load_dotenv()
    pending = find_episodes_by_status("awaiting_review", slot=slot)
    reminded: list[str] = []
    brand = get_brand_name()
    for row in pending:
        if row.get("reminder_sent"):
            continue
        ep_id = str(row.get("episode_id") or "")
        send_telegram_message(
            f"⏰ [{brand}] 15 minutes until AI Chief Editor takes over!\n"
            f"• Slot: {row.get('slot')} | Title: {row.get('topic_title')}\n"
            f"• Unlisted YT: {row.get('youtube_url') or '-'}\n"
            f"• Tap one of the 4 buttons on the audit card above to Approve / Revise / Reject manually."
        )
        row["reminder_sent"] = True
        upsert_pending_review(row)
        reminded.append(ep_id)
    return reminded


def run_scheduled_ai_fallback(slot: Optional[str] = None) -> list[dict[str, Any]]:
    """Run AI Chief Editor audit on all untouched `awaiting_review` episodes for the slot."""
    load_dotenv()
    if os.environ.get("ENABLE_AI_FALLBACK_AUDIT", "true").strip().lower() != "true":
        print("[chief_editor] ENABLE_AI_FALLBACK_AUDIT is false; skipping scheduled AI fallback.")
        return []
    pending = find_episodes_by_status("awaiting_review", slot=slot)
    results: list[dict[str, Any]] = []
    for row in pending:
        ep_id = str(row.get("episode_id") or "")
        if not ep_id:
            continue
        try:
            results.append(run_chief_editor_audit_episode(ep_id))
        except Exception as exc:
            send_telegram_message(f"⚠️ [{get_brand_name()}] AI Chief Editor error on {ep_id}: {str(exc)[:180]}")
            results.append({"episode_id": ep_id, "error": str(exc)})
    return results


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="AI Chief Editor Multimodal Audit & Fallback")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--episode-id", required=True)

    p_rem = sub.add_parser("remind")
    p_rem.add_argument("--slot", default=None)

    p_fb = sub.add_parser("fallback")
    p_fb.add_argument("--slot", default=None)

    args = parser.parse_args()
    if args.cmd == "audit":
        out = run_chief_editor_audit_episode(args.episode_id)
    elif args.cmd == "remind":
        out = {"reminded": send_review_reminders(slot=args.slot or None)}
    else:
        out = {"audited": run_scheduled_ai_fallback(slot=args.slot or None)}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
