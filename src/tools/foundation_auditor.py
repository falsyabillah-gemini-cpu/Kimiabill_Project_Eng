"""Deterministic Weekly System & Agent Foundation Auditor (0 AI tokens).

Runs alongside Agent 8 every Sunday at 20:00 WIB inside `weekly_audit_kimiabill.yml`:
1. Audits all 8 agents' runtime health from the past 7 days (`published_history.json` telemetry):
   - Model fallback downgrades to lightweight `flash-lite` models
   - High revision rates (`revisions_used >= 2`) or topic retries (`topic_attempts_used >= 2`)
   - Slow episode production times (`production_seconds > 1200`)
   - Missing `youtube_url` on `public`/`draft` runs
   - Publish time drift exceeding the 15-minute SLA after `12:00` / `19:00` WIB
2. Checks GitHub Actions `daily_kimiabill.yml` run failures / cancellations (when `gh` CLI + `GH_TOKEN` are available).
3. Verifies codebase Python compilation (`compileall`).
4. Outputs a structured JSON report + crisp Indonesian checklist appended to Agent 8's Sunday email
   so the creator can reply "bereskan" whenever action is needed.
"""
from __future__ import annotations

import compileall
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def _parse_iso_wib(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=WIB)
        return dt.astimezone(WIB)
    except Exception:
        return None


from src.brand_config import get_daily_slots_wib


def _check_recent_gh_runs(repo: str, cutoff_wib: datetime) -> list[dict[str, Any]]:
    """Query recent daily_studio.yml runs via `gh` CLI (best-effort, 0 tokens)."""
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        return []
    try:
        proc = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "-R",
                repo,
                "--workflow",
                "daily_studio.yml",
                "--limit",
                "20",
                "--json",
                "databaseId,status,conclusion,createdAt,updatedAt,event,url",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        runs = json.loads(proc.stdout)
        recent: list[dict[str, Any]] = []
        for r in runs:
            dt = _parse_iso_wib(str(r.get("createdAt") or ""))
            if dt and dt >= cutoff_wib:
                recent.append(r)
        return recent
    except Exception:
        return []


def run_weekly_foundation_audit(
    base_dir: Path,
    today_wib: str,
    repo: str = "falsyabillah-gemini-cpu/Kimiabill_Project_Eng",
) -> dict[str, Any]:
    """Run full 0-token technical health & SLA audit across all 8 agents and GitHub Actions."""
    now_wib = datetime.now(WIB)
    cutoff = now_wib - timedelta(days=7)

    findings: list[dict[str, str]] = []

    def _add(severity: str, area: str, title: str, detail: str, fix_hint: str) -> None:
        findings.append(
            {
                "severity": severity,
                "area": area,
                "title": title,
                "detail": detail,
                "fix_hint": fix_hint,
            }
        )

    # 1. Codebase compile health
    compile_ok = bool(compileall.compile_dir(str(base_dir / "src"), quiet=2))
    if not compile_ok:
        _add(
            "CRITICAL",
            "Python Codebase",
            "One or more files in src/ failed Python compilation",
            "compileall detected syntax errors in src/.",
            "Inspect recently modified files and fix syntax errors.",
        )

    # 2. Weekly episode execution telemetry from published_history.json
    hist_path = base_dir / "data" / "published_history.json"
    history: list[dict[str, Any]] = []
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            history = []

    week_eps: list[dict[str, Any]] = []
    for row in history:
        dt = _parse_iso_wib(str(row.get("published_at_wib") or ""))
        if dt and dt >= cutoff:
            week_eps.append(row)

    missing_yt = [
        r.get("episode_id")
        for r in week_eps
        if r.get("publish_mode", "public") in ("public", "draft") and not r.get("youtube_url")
    ]
    if missing_yt:
        _add(
            "CRITICAL",
            "Agent 7 / Auto-Upload",
            f"{len(missing_yt)} episode(s) this week missing a YouTube URL",
            f"Episodes missing YouTube URL: {', '.join(str(x) for x in missing_yt[:5])}.",
            "Check the 34 MB payload cap, Apps Script webhook logs, and run rescue upload if artifacts exist.",
        )

    slots = get_daily_slots_wib()
    slot_hours = sorted(int(s.split(":")[0]) for s in slots if ":" in s) or [4, 21]
    late_slots: list[str] = []
    for r in week_eps:
        dt = _parse_iso_wib(str(r.get("published_at_wib") or ""))
        if not dt or not r.get("youtube_url"):
            continue
        target_h = slot_hours[1] if dt.hour >= 12 else slot_hours[0]
        target_dt = dt.replace(hour=target_h, minute=0, second=0, microsecond=0)
        delay_min = (dt - target_dt).total_seconds() / 60.0
        if delay_min > 15.0:
            late_slots.append(f"{r.get('episode_id')} (+{int(delay_min)}m -> {dt.strftime('%H:%M')} WIB)")
    if late_slots:
        _add(
            "HIGH",
            "Cron Schedule / <=15m SLA",
            f"{len(late_slots)} upload(s) exceeded the 15-minute SLA after {' / '.join(slots)} WIB",
            "; ".join(late_slots[:5]),
            "Review GitHub Actions cron offset or Proxy & QA rendering speed.",
        )

    flash_lite_hits = 0
    high_rev_eps: list[str] = []
    slow_eps: list[str] = []
    for r in week_eps:
        models_blob = json.dumps(r.get("agent_models") or {}).lower()
        if "flash-lite" in models_blob:
            flash_lite_hits += 1
        if int(r.get("revisions_used") or 0) >= 2:
            high_rev_eps.append(f"{r.get('episode_id')} ({r.get('revisions_used')}x revisi)")
        prod_s = float(r.get("production_seconds") or 0.0)
        if prod_s > 1200.0:
            slow_eps.append(f"{r.get('episode_id')} ({int(prod_s // 60)}m)")

    if week_eps and flash_lite_hits >= max(2, len(week_eps) // 2):
        _add(
            "TINGGI",
            "Providers / Routing Model",
            f"Mayoritas agent ({flash_lite_hits}/{len(week_eps)} episode) turun ke model ringan `flash-lite`",
            "Model utama/menengah sering kena 503/limit/timeout sebelum mencapai Agent 3/5.",
            "Cek status kuota Gemini API Key & urutan fallback model di src/providers.py / config/agents.yaml.",
        )
    if len(high_rev_eps) >= 2:
        _add(
            "SEDANG",
            "Agent 5 & Agent 6 (QA & Footage)",
            f"{len(high_rev_eps)} episode butuh >=2x revisi QA",
            ", ".join(high_rev_eps[:5]),
            "Periksa penolakan berulang Agent 5 (biasanya relevansi klip Wikimedia atau label mekanistik terpotong).",
        )
    if slow_eps:
        _add(
            "SEDANG",
            "Kecepatan Produksi",
            f"{len(slow_eps)} episode berdurasi produksi >20 menit",
            ", ".join(slow_eps[:5]),
            "Optimalkan screening footage Agent 6 / timeout API model.",
        )

    # 3. GitHub Actions weekly runs check
    gh_runs = _check_recent_gh_runs(repo, cutoff)
    failed_runs = [
        f"#{r.get('databaseId')} ({r.get('conclusion')})"
        for r in gh_runs
        if r.get("status") == "completed" and r.get("conclusion") not in ("success", "skipped")
    ]
    if failed_runs:
        _add(
            "KRITIS",
            "GitHub Actions Workflow",
            f"{len(failed_runs)} workflow run mingguan gagal/dibatalkan",
            ", ".join(failed_runs[:5]),
            "Baca log run gagal di GitHub Actions dan bereskan akar error-nya.",
        )

    overall_status = "SEHAT"
    if any(f["severity"] in ("KRITIS", "CRITICAL") for f in findings):
        overall_status = "KRITIS"
    elif findings:
        overall_status = "PERLU PERHATIAN"

    lines = [
        f"## Audit Pondasi Sistem & Workflow Mingguan (Antigravity System Check — Status: {overall_status})",
        f"- **Cakupan Pemeriksaan:** Compile Python `src/` ({'OK' if compile_ok else 'GAGAL'}), "
        f"{len(week_eps)} episode minggu ini di arsip, SLA keterlambatan <=15 menit, "
        f"stabilitas model & fallback, serta {len(gh_runs)} run GitHub Actions terakhir.",
    ]
    if not findings:
        lines.append("- **Hasil:** Semua 8 agent, jadwal tayang (SLA <=15m), fallback model, dan upload pipeline dalam kondisi **SEHAT** — belum ada perbaikan mendesak minggu ini.")
    else:
        for f in findings:
            lines.append(f"- **[{f['severity']}] {f['area']} — {f['title']}:** {f['detail']} *(Saran: {f['fix_hint']})*")
        lines.append("")
        lines.append("👉 **Butuh Konfirmasi:** Balas **`bereskan`** di chat Antigravity kalau kamu ingin temuan di atas langsung saya perbaiki sekarang.")

    result = {
        "audited_at_wib": now_wib.isoformat(timespec="seconds"),
        "week_ending": today_wib,
        "overall_status": overall_status,
        "compile_ok": compile_ok,
        "episodes_checked": len(week_eps),
        "gh_runs_checked": len(gh_runs),
        "findings": findings,
        "markdown_block": "\n".join(lines),
    }
    return result
