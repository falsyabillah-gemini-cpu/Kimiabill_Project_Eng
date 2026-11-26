"""Executive Visual PDF Report Generator for Agent 8 Weekly Audit (Pillow-only, 0 tokens).

Generates a crisp 5-page A4 visual PDF (`WEEKLY_AUDIT_YYYY-MM-DD.pdf`) with:
- Page 1: Branded header, 4 KPI cards, YouTube Partner Program (YPP) monetization
          progress bars, horizontal bar chart of recent video views, executive summary
          & workflow/token efficiency summary.
- Page 2: Top patterns vs weaknesses, token/cost optimizations, color-coded upgrade
          directives for Agents 1, 2, 3, 5, 6, viewer requests, KARAT directives & experiments.
- Page 3: International viewer potential & cumulative SQLite milestone database.
- Page 4: Topic Landscape — Ranked Top vs Bottom topics ("Paling Rame vs Paling Sepi"),
          5-Discipline Topic Cluster Map, 2D Quadrant Performance Plot, and key findings.
- Page 5: Next-Week 7-Day × 2-Slot Visual Topic Roadmap (12:00 DAPUR / 19:00 REGULER & KARAT),
          discipline balance bar, and full produced topic catalog index.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from src.brand_config import get_brand_name
from src.state import Agent8WeeklyAuditOutput

A4_W, A4_H = 1240, 1754  # A4 @ 150 DPI
BG = (245, 247, 251)
WHITE = (255, 255, 255)
NAVY = (17, 24, 39)
SLATE = (71, 85, 105)
MUTED = (148, 163, 184)
BORDER = (226, 232, 240)
BLUE = (37, 99, 235)
CYAN = (8, 145, 178)
GREEN = (22, 163, 74)
AMBER = (217, 119, 6)
RED = (220, 38, 38)
PURPLE = (124, 58, 237)
YELLOW = (250, 204, 21)

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_REG_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _pick_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "h1": _pick_font(FONT_BOLD_CANDIDATES, 36),
        "h2": _pick_font(FONT_BOLD_CANDIDATES, 24),
        "h3": _pick_font(FONT_BOLD_CANDIDATES, 20),
        "kpi": _pick_font(FONT_BOLD_CANDIDATES, 34),
        "body": _pick_font(FONT_REG_CANDIDATES, 18),
        "body_b": _pick_font(FONT_BOLD_CANDIDATES, 18),
        "small": _pick_font(FONT_REG_CANDIDATES, 16),
        "small_b": _pick_font(FONT_BOLD_CANDIDATES, 16),
        "micro": _pick_font(FONT_REG_CANDIDATES, 13),
        "micro_b": _pick_font(FONT_BOLD_CANDIDATES, 13),
    }


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int = 8) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), cand, font=font)[2] <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines or ["-"]


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill=WHITE, outline=BORDER, radius: int = 18) -> None:
    draw.rounded_rectangle(list(box), radius=radius, fill=fill, outline=outline, width=2)


def _pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    fill: tuple[int, int, int],
    text_fill: tuple[int, int, int],
    font: ImageFont.FreeTypeFont,
    pad_x: int = 10,
    pad_y: int = 4,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pw = tw + pad_x * 2
    ph = th + pad_y * 2 + 2
    draw.rounded_rectangle([x, y, x + pw, y + ph], radius=ph // 2, fill=fill)
    draw.text((x + pad_x, y + pad_y - 1), text, font=font, fill=text_fill)
    return x + pw


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    label: str,
    val: int,
    target: int,
    color: tuple[int, int, int],
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> int:
    pct = min(100.0, (val / max(1, target)) * 100.0)
    left = max(0, target - val)
    draw.text((x, y), label, font=fonts["small_b"], fill=NAVY)
    right_txt = f"{val:,} / {target:,} ({pct:.2f}%) • kurang {left:,}"
    tw = draw.textbbox((0, 0), right_txt, font=fonts["small"])[2]
    draw.text((x + w - tw, y), right_txt, font=fonts["small"], fill=SLATE)
    by = y + 26
    draw.rounded_rectangle([x, by, x + w, by + 22], radius=11, fill=(226, 232, 240))
    fill_w = max(18 if val > 0 else 0, int(w * (pct / 100.0)))
    if fill_w > 0:
        draw.rounded_rectangle([x, by, x + fill_w, by + 22], radius=11, fill=color)
    return by + 36


def generate_weekly_audit_pdf(
    audit: Agent8WeeklyAuditOutput,
    enriched_videos: list[dict[str, Any]],
    channel_stats: dict[str, Any],
    out_pdf: Path,
    countries_summary: dict[str, Any] | None = None,
    milestone_stats: dict[str, Any] | None = None,
    topic_landscape: dict[str, Any] | None = None,
) -> Path:
    """Render a 5-page executive visual PDF report (KPI, upgrades, global DB, Topic Map & Next-Week Roadmap)."""
    fonts = _load_fonts()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # ======================== PAGE 1: KPI, MONETIZATION & CHARTS ========================
    p1 = Image.new("RGB", (A4_W, A4_H), BG)
    d1 = ImageDraw.Draw(p1)

    from src.brand_config import get_brand_name
    brand_name = get_brand_name()

    # Header Banner
    d1.rectangle([0, 0, A4_W, 155], fill=NAVY)
    d1.text((56, 36), f"{brand_name.upper()} STUDIO • WEEKLY AUDIT REPORT", font=fonts["h1"], fill=YELLOW)
    d1.text(
        (56, 92),
        f"Agent 8 (Chief Auditor)  |  Week Ending: {audit.week_ending}  |  Channel: {channel_stats.get('title', brand_name)}",
        font=fonts["body"],
        fill=(203, 213, 225),
    )

    # 4 KPI Cards
    subs = int(channel_stats.get("subscribers") or 0)
    tot_views = int(channel_stats.get("total_views") or 0)
    vid_count = int(channel_stats.get("video_count") or len(enriched_videos))
    qa_scores = [float(v["qa_score"]) for v in enriched_videos if v.get("qa_score") is not None]
    avg_qa = f"{sum(qa_scores) / len(qa_scores):.1f}/10" if qa_scores else "N/A"

    kpis = [
        ("SUBSCRIBER", f"{subs:,}", BLUE),
        ("TOTAL VIEWS", f"{tot_views:,}", CYAN),
        ("TOTAL VIDEO", f"{vid_count:,}", PURPLE),
        ("RATA-RATA QA", avg_qa, GREEN),
    ]
    cw = 266
    gap = 21
    for i, (k_lbl, k_val, k_col) in enumerate(kpis):
        kx0 = 56 + i * (cw + gap)
        _card(d1, (kx0, 185, kx0 + cw, 295))
        d1.rounded_rectangle([kx0, 185, kx0 + 8, 295], radius=4, fill=k_col)
        d1.text((kx0 + 24, 203), k_lbl, font=fonts["small_b"], fill=SLATE)
        d1.text((kx0 + 24, 236), k_val, font=fonts["kpi"], fill=NAVY)

    # Monetization Progress Card (YPP Shorts)
    my0 = 320
    _card(d1, (56, my0, A4_W - 56, my0 + 290))
    d1.text((84, my0 + 22), "PROGRES MENUJU MONETISASI YOUTUBE SHORTS (YPP)", font=fonts["h2"], fill=NAVY)
    cy = my0 + 64
    cy = _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 1 • Fan Funding — Target 500 Subscriber", subs, 500, BLUE, fonts)
    cy = _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 1 • Fan Funding — Target 3 Juta Views (90 hari)", tot_views, 3_000_000, CYAN, fonts)
    cy = _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 2 • Iklan Shorts — Target 1.000 Subscriber", subs, 1000, GREEN, fonts)
    _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 2 • Iklan Shorts — Target 10 Juta Views (90 hari)", tot_views, 10_000_000, AMBER, fonts)

    # Bar Chart: Views per Recent Video
    by0 = 635
    _card(d1, (56, by0, A4_W - 56, by0 + 425))
    d1.text((84, by0 + 22), "VISUALISASI PERFORMA VIDEO TERBARU (VIEWS & RETENSI)", font=fonts["h2"], fill=NAVY)
    top_vids = enriched_videos[:7]
    if top_vids:
        max_v = max(1, max(int(v.get("views") or 0) for v in top_vids))
        bar_colors = [BLUE, CYAN, PURPLE, GREEN, AMBER, RED, (14, 116, 144)]
        vy = by0 + 72
        for idx, v in enumerate(top_vids):
            v_views = int(v.get("views") or 0)
            title = (v.get("title") or v.get("episode_id") or "Video")[:38]
            ret = v.get("avg_view_percentage")
            ret_txt = f" | Ret: {ret}%" if ret is not None else ""
            d1.text((84, vy + 4), title, font=fonts["small_b"], fill=NAVY)
            bar_x0 = 490
            bar_max_w = 420
            bw = max(14, int(bar_max_w * (v_views / max_v)))
            d1.rounded_rectangle([bar_x0, vy + 2, bar_x0 + bar_max_w, vy + 28], radius=8, fill=(238, 242, 247))
            d1.rounded_rectangle([bar_x0, vy + 2, bar_x0 + bw, vy + 28], radius=8, fill=bar_colors[idx % len(bar_colors)])
            d1.text((bar_x0 + bar_max_w + 16, vy + 4), f"{v_views:,} views{ret_txt}", font=fonts["small_b"], fill=SLATE)
            vy += 48
    else:
        d1.text((84, by0 + 90), "Belum ada data video minggu ini.", font=fonts["body"], fill=SLATE)

    # Executive Summary Card
    sy0 = 1085
    _card(d1, (56, sy0, A4_W - 56, sy0 + 280))
    d1.text((84, sy0 + 22), "RINGKASAN PERFORMA MINGGU INI", font=fonts["h2"], fill=BLUE)
    ty = sy0 + 62
    for ln in _wrap(d1, audit.summary, fonts["body"], A4_W - 168, max_lines=8):
        d1.text((84, ty), ln, font=fonts["body"], fill=NAVY)
        ty += 25

    # Workflow & Token Efficiency Card
    wy0 = 1390
    _card(d1, (56, wy0, A4_W - 56, A4_H - 56))
    d1.text((84, wy0 + 22), "AUDIT EFISIENSI WORKFLOW & PENGGUNAAN TOKEN", font=fonts["h2"], fill=AMBER)
    ty = wy0 + 62
    for ln in _wrap(d1, audit.workflow_efficiency_summary or "-", fonts["body"], A4_W - 168, max_lines=8):
        d1.text((84, ty), ln, font=fonts["body"], fill=NAVY)
        ty += 25

    # ======================== PAGE 2: ACTION PLAN & AGENT UPGRADES ========================
    p2 = Image.new("RGB", (A4_W, A4_H), BG)
    d2 = ImageDraw.Draw(p2)
    d2.rectangle([0, 0, A4_W, 110], fill=NAVY)
    d2.text((56, 34), "RENCANA PERBAIKAN & UPGRADE OTOMATIS SELURUH AGENT", font=fonts["h2"], fill=YELLOW)
    d2.text((56, 72), f"Diterapkan otomatis mulai minggu berikutnya  •  {get_brand_name()} ({audit.week_ending})", font=fonts["small"], fill=(203, 213, 225))

    # Top Patterns vs Weaknesses (2 columns)
    col_w = 550
    _card(d2, (56, 138, 56 + col_w, 468))
    d2.text((82, 158), "POLA PALING BERHASIL", font=fonts["h3"], fill=GREEN)
    ty = 196
    for item in (audit.top_performing_patterns or ["-"])[:4]:
        for ln in _wrap(d2, f"• {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > 442:
                break
            d2.text((82, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 22
        ty += 6

    rx0 = 56 + col_w + 28
    _card(d2, (rx0, 138, A4_W - 56, 468))
    d2.text((rx0 + 26, 158), "HAMBATAN ENGAGEMENT", font=fonts["h3"], fill=RED)
    ty = 196
    for item in (audit.bottlenecks_and_weaknesses or ["-"])[:4]:
        for ln in _wrap(d2, f"• {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > 442:
                break
            d2.text((rx0 + 26, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 22
        ty += 6

    # Token & Cost Optimizations
    _card(d2, (56, 492, A4_W - 56, 720))
    d2.text((82, 512), "OPTIMASI TOKEN, KUOTA & KECEPATAN PRODUKSI", font=fonts["h3"], fill=AMBER)
    ty = 550
    for item in (audit.token_and_cost_optimizations or ["-"])[:4]:
        for ln in _wrap(d2, f"• {item}", fonts["small"], A4_W - 168, max_lines=2):
            if ty > 695:
                break
            d2.text((82, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 22
        ty += 6

    # Cross-Agent Directives Card
    _card(d2, (56, 744, A4_W - 56, 1442))
    d2.text((82, 762), "INSTRUKSI UPGRADE OTOMATIS KE SELURUH AGENT", font=fonts["h2"], fill=NAVY)
    agent_blocks = [
        ("AGENT 1 (Trend & Ideasi)", BLUE, audit.agent_1_directives),
        ("AGENT 2 (Auditor Hook)", PURPLE, audit.agent_2_directives),
        ("AGENT 3 (Skrip & Animasi Mekanistik)", CYAN, audit.agent_3_directives),
        ("AGENT 4 (Animator Mekanistik)", GREEN, getattr(audit, "agent_4_animator_directives", [])),
        ("AGENT 5 (Video QA Auditor)", RED, audit.agent_5_directives),
        ("AGENT 6 (Pencari Footage Real)", AMBER, audit.agent_6_directives),
    ]
    ay = 804
    for label, col, items in agent_blocks:
        if ay > 1406:
            break
        d2.rounded_rectangle([82, ay, 475, ay + 28], radius=8, fill=col)
        d2.text((96, ay + 5), label, font=fonts["small_b"], fill=WHITE)
        ay += 34
        for it in (items or ["Maintain current standard."])[:2]:
            for ln in _wrap(d2, f"→ {it}", fonts["small"], A4_W - 180, max_lines=1):
                if ay > 1426:
                    break
                d2.text((92, ay), ln, font=fonts["small"], fill=NAVY)
                ay += 20
        ay += 6

    # Bottom Row: Next Week Experiments + Viewer Requests (Left) + KARAT Weekend Research Recommendations (Right)
    _card(d2, (56, 1455, 56 + col_w, A4_H - 56))
    d2.text((82, 1475), "EKSPERIMEN & REQUEST PENONTON", font=fonts["h3"], fill=GREEN)
    ty = 1511
    for req in (getattr(audit, "audience_requests", None) or [])[:2]:
        req_line = f"► Request {req.requester_name}: {req.topic_title}"
        for ln in _wrap(d2, req_line, fonts["small_b"], col_w - 52, max_lines=2):
            if ty > A4_H - 72:
                break
            d2.text((82, ty), ln, font=fonts["small_b"], fill=BLUE)
            ty += 21
        ty += 4
    for item in (audit.next_week_experiments or ["-"])[:3]:
        for ln in _wrap(d2, f"★ {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > A4_H - 72:
                break
            d2.text((82, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 21
        ty += 5

    _card(d2, (rx0, 1455, A4_W - 56, A4_H - 56))
    d2.text((rx0 + 26, 1475), "WEEKEND TECH RADAR DIRECTIVES", font=fonts["h3"], fill=(234, 88, 12))
    ty = 1511
    for item in (getattr(audit, "tech_radar_weekend_directives", None) or ["Cover latest everyday tech breakthroughs from top performing pillars."])[:3]:
        for ln in _wrap(d2, f"◆ {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > A4_H - 72:
                break
            d2.text((rx0 + 26, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 21
        ty += 5

    # ======================== PAGE 3: GLOBAL AUDIENCE & MILESTONE DATABASE ========================
    cs = countries_summary or {}
    ms = milestone_stats or {}
    p3 = Image.new("RGB", (A4_W, A4_H), BG)
    d3 = ImageDraw.Draw(p3)
    d3.rectangle([0, 0, A4_W, 110], fill=NAVY)
    d3.text((56, 34), "GLOBAL AUDIENCE GEOGRAPHY & MILESTONE DATABASE", font=fonts["h2"], fill=YELLOW)
    d3.text(
        (56, 72),
        f"Cumulative Long-Term Tracking (SQLite + Master CSV)  •  Week Ending {audit.week_ending}",
        font=fonts["small"],
        fill=(203, 213, 225),
    )

    r_avg_str = f"{ms['radar_avg_views']:.0f}" if ms.get("radar_avg_views") is not None else "-"
    e_avg_str = f"{ms['everyday_avg_views']:.0f}" if ms.get("everyday_avg_views") is not None else "-"
    db_kpis = [
        ("ARCHIVED EPISODES", f"{int(ms.get('total_videos_archived') or len(enriched_videos)):,}", BLUE),
        ("WEEKS AUDITED", f"{int(ms.get('total_weeks_audited') or 1):,}", PURPLE),
        ("TECH RADAR AVG", r_avg_str, (234, 88, 12)),
        ("EVERYDAY TECH AVG", e_avg_str, CYAN),
    ]
    for i, (k_lbl, k_val, k_col) in enumerate(db_kpis):
        kx0 = 56 + i * (cw + gap)
        _card(d3, (kx0, 140, kx0 + cw, 250))
        d3.rounded_rectangle([kx0, 140, kx0 + 8, 250], radius=4, fill=k_col)
        d3.text((kx0 + 24, 158), k_lbl, font=fonts["small_b"], fill=SLATE)
        d3.text((kx0 + 24, 191), k_val, font=fonts["kpi"], fill=NAVY)

    # Top Countries Card
    cy0 = 278
    _card(d3, (56, cy0, A4_W - 56, cy0 + 440))
    d3.text((84, cy0 + 22), "TOP VIEWER COUNTRIES (LAST 28 DAYS)", font=fonts["h2"], fill=NAVY)
    top_countries = cs.get("top_countries") or []
    if top_countries:
        max_cv = max(1, max(int(c.get("views") or 0) for c in top_countries[:8]))
        tot_cv = max(1, int(cs.get("analytics_total_views") or sum(int(c.get("views") or 0) for c in top_countries)))
        vy = cy0 + 74
        c_colors = [BLUE, CYAN, GREEN, PURPLE, AMBER, RED, (14, 116, 144), SLATE]
        for idx, c in enumerate(top_countries[:8]):
            code = str(c.get("country") or "-")
            c_views = int(c.get("views") or 0)
            pct_c = (c_views / tot_cv) * 100.0
            d3.text((84, vy + 4), f"Country {code}", font=fonts["small_b"], fill=NAVY)
            bx0, bmax = 310, 580
            bw = max(14 if c_views > 0 else 0, int(bmax * (c_views / max_cv)))
            d3.rounded_rectangle([bx0, vy + 2, bx0 + bmax, vy + 28], radius=8, fill=(238, 242, 247))
            if bw > 0:
                d3.rounded_rectangle([bx0, vy + 2, bx0 + bw, vy + 28], radius=8, fill=c_colors[idx % len(c_colors)])
            d3.text((bx0 + bmax + 18, vy + 4), f"{c_views:,} views ({pct_c:.1f}%)", font=fonts["small_b"], fill=SLATE)
            vy += 44
    else:
        d3.text((84, cy0 + 84), "Country breakdown not yet available for this cycle.", font=fonts["body"], fill=SLATE)

    # Global Geo Insights Card
    iy0 = 746
    _card(d3, (56, iy0, A4_W - 56, iy0 + 380))
    d3.text((84, iy0 + 22), "GLOBAL AUDIENCE GEOGRAPHY & TIMEZONE INSIGHTS", font=fonts["h2"], fill=CYAN)
    intl_txt = (getattr(audit, "audience_geo_insights", "") or "").strip() or (
        "Track US, UK, CA, AU, EU, and global English viewer shares alongside peak upload hours (04:00 & 21:00 WIB)."
    )
    ty = iy0 + 66
    for ln in _wrap(d3, intl_txt, fonts["body"], A4_W - 168, max_lines=10):
        d3.text((84, ty), ln, font=fonts["body"], fill=NAVY)
        ty += 26

    # Cumulative Milestone Database Card
    dy0 = 1154
    _card(d3, (56, dy0, A4_W - 56, A4_H - 56))
    d3.text((84, dy0 + 22), "CUMULATIVE MILESTONE RECAP", font=fonts["h2"], fill=PURPLE)
    e_avg_v = f"{ms['everyday_avg_views']:.1f} views" if ms.get("everyday_avg_views") is not None else "insufficient data"
    r_avg_v = f"{ms['radar_avg_views']:.1f} views" if ms.get("radar_avg_views") is not None else "insufficient data"
    e_avg_ret = f"{ms['everyday_avg_retention_pct']:.1f}%" if ms.get("everyday_avg_retention_pct") is not None else "-"
    r_avg_ret = f"{ms['radar_avg_retention_pct']:.1f}%" if ms.get("radar_avg_retention_pct") is not None else "-"
    milestone_lines = [
        f"• Archived Episodes: {int(ms.get('total_videos_archived') or len(enriched_videos))} total (Everyday Tech: {ms.get('everyday_videos', 0)} | Tech Radar: {ms.get('radar_videos', 0)})",
        f"• Segment Comparison: EVERYDAY_TECH avg {e_avg_v} (retention {e_avg_ret})  vs  TECH_RADAR avg {r_avg_v} (retention {r_avg_ret})",
        f"• Top Performing Pillar (Cumulative): {ms.get('top_discipline', '-')} (avg {ms.get('top_discipline_avg_views', 0.0):.1f} views/video)",
        "• Master Database CSV: STUDIO_MASTER_DATABASE.csv is updated weekly in GitHub & Google Drive (Weekly_Audits).",
    ]
    ty = dy0 + 72
    for item in milestone_lines:
        for ln in _wrap(d3, item, fonts["body"], A4_W - 168, max_lines=3):
            if ty > A4_H - 80:
                break
            d3.text((84, ty), ln, font=fonts["body"], fill=NAVY)
            ty += 26
        ty += 12

    # Helper dicts for pillar colors
    disc_colors: dict[str, tuple[int, int, int]] = {
        "gadgets": BLUE,
        "engineering": PURPLE,
        "materials": AMBER,
        "energy": GREEN,
        "biotech": CYAN,
    }
    sec_colors: dict[str, tuple[int, int, int]] = {
        "EVERYDAY_TECH": BLUE,
        "TECH_RADAR": (234, 88, 12),
    }

    tl = dict(topic_landscape or {})
    if not tl.get("all_topics"):
        fallback_items = [
            {
                "title": str(v.get("title") or v.get("episode_id") or "Video"),
                "discipline": str(v.get("discipline") or "gadgets").lower() if str(v.get("discipline") or "-") not in ("-", "") else "gadgets",
                "section": str(v.get("section") or "EVERYDAY_TECH").upper(),
                "views": int(v.get("views") or 0),
                "likes": int(v.get("likes") or 0),
                "tier": "TOP" if int(v.get("views") or 0) >= 150 else ("MID" if int(v.get("views") or 0) > 10 else "LOW"),
            }
            for v in enriched_videos
        ]
        fallback_items.sort(key=lambda x: x["views"], reverse=True)
        tl["all_topics"] = fallback_items
        tl["total_science_videos"] = len(fallback_items)
        tl["top_videos"] = fallback_items[:5]
        tl["bottom_videos"] = sorted(fallback_items, key=lambda x: (x["views"], x["likes"]))[:5]
        pillars = ["gadgets", "engineering", "materials", "energy", "biotech"]
        tl["discipline_breakdown"] = []
        for p in pillars:
            its = [t for t in fallback_items if t["discipline"] == p]
            tot_v = sum(i["views"] for i in its)
            tl["discipline_breakdown"].append({
                "discipline": p,
                "count": len(its),
                "total_views": tot_v,
                "avg_views": round(tot_v / len(its), 1) if its else 0.0,
                "topics": its,
            })
        tl["section_breakdown"] = [
            {"section": s, "count": sum(1 for t in fallback_items if t["section"] == s), "avg_views": 0.0}
            for s in ("EVERYDAY_TECH", "TECH_RADAR")
        ]

    # ======================== PAGE 4: TOPIC LANDSCAPE, RAME VS SEPI & QUADRANT MAP ========================
    p4 = Image.new("RGB", (A4_W, A4_H), BG)
    d4 = ImageDraw.Draw(p4)
    d4.rectangle([0, 0, A4_W, 110], fill=NAVY)
    d4.text((56, 30), "PETA TOPIK VIDEO & ANALISIS RAME VS SEPI", font=fonts["h2"], fill=YELLOW)
    d4.text(
        (56, 68),
        f"Total {tl.get('total_science_videos', 0)} Topik Sains Terpetakan  •  Pemenang vs Evaluasi, Klaster 5 Disiplin & Peta Kuadran ({audit.week_ending})",
        font=fonts["small"],
        fill=(203, 213, 225),
    )

    # Top Row Charts: TOP 5 RAME (left) vs TOP 5 SEPI (right)
    _card(d4, (56, 134, 56 + col_w, 476))
    d4.rounded_rectangle([56, 134, 64, 476], radius=4, fill=GREEN)
    d4.text((82, 152), "TOP 5 TOPIK PALING RAME", font=fonts["h3"], fill=GREEN)
    _pill(d4, 456, 150, "PEMENANG", (220, 252, 231), GREEN, fonts["micro_b"])
    top_list = tl.get("top_videos") or []
    if top_list:
        max_tv = max(1, max(int(t.get("views") or 0) for t in top_list))
        ty = 196
        for idx, t in enumerate(top_list[:5], 1):
            tv = int(t.get("views") or 0)
            tlk = int(t.get("likes") or 0)
            disc = str(t.get("discipline") or "kimia").upper()[:5]
            d4.text((82, ty), f"#{idx} {str(t.get('title') or '-')[:34]}", font=fonts["small_b"], fill=NAVY)
            d4.text((82, ty + 22), f"{disc} • {t.get('section', 'REGULER')}", font=fonts["micro"], fill=SLATE)
            bx0, bmax = 260, 200
            bw = max(14 if tv > 0 else 6, int(bmax * (tv / max_tv)))
            d4.rounded_rectangle([bx0, ty + 21, bx0 + bmax, ty + 37], radius=7, fill=(238, 242, 247))
            d4.rounded_rectangle([bx0, ty + 21, bx0 + bw, ty + 37], radius=7, fill=GREEN if idx == 1 else CYAN)
            d4.text((bx0 + bmax + 10, ty + 19), f"{tv:,}v • {tlk}L", font=fonts["micro_b"], fill=NAVY)
            ty += 54
    else:
        d4.text((82, 220), "Belum ada data topik video.", font=fonts["body"], fill=SLATE)

    _card(d4, (rx0, 134, A4_W - 56, 476))
    d4.rounded_rectangle([rx0, 134, rx0 + 8, 476], radius=4, fill=RED)
    d4.text((rx0 + 26, 152), "TOPIK PALING SEPI", font=fonts["h3"], fill=RED)
    _pill(d4, A4_W - 196, 150, "PERLU EVALUASI", (254, 226, 226), RED, fonts["micro_b"])
    bot_list = tl.get("bottom_videos") or []
    if bot_list:
        ref_max = max(1, max([int(t.get("views") or 0) for t in top_list] + [100]))
        ty = 196
        for idx, b in enumerate(bot_list[:5], 1):
            bv = int(b.get("views") or 0)
            blk = int(b.get("likes") or 0)
            disc = str(b.get("discipline") or "kimia").upper()[:5]
            d4.text((rx0 + 26, ty), f"▼ {str(b.get('title') or '-')[:34]}", font=fonts["small_b"], fill=NAVY)
            d4.text((rx0 + 26, ty + 22), f"{disc} • {b.get('section', 'REGULER')}", font=fonts["micro"], fill=SLATE)
            bx0, bmax = rx0 + 204, 195
            bw = max(8, int(bmax * (bv / ref_max)))
            d4.rounded_rectangle([bx0, ty + 21, bx0 + bmax, ty + 37], radius=7, fill=(254, 242, 242))
            d4.rounded_rectangle([bx0, ty + 21, bx0 + bw, ty + 37], radius=7, fill=RED if bv <= 5 else AMBER)
            d4.text((bx0 + bmax + 10, ty + 19), f"{bv:,}v • {blk}L", font=fonts["micro_b"], fill=RED if bv <= 5 else AMBER)
            ty += 54
    else:
        d4.text((rx0 + 26, 220), "Belum ada data topik video.", font=fonts["body"], fill=SLATE)

    # Middle Left: 5-Discipline Cluster Topic Map
    map_x1 = 716
    _card(d4, (56, 498, map_x1, 1142))
    d4.text((80, 516), "PETA KLASTER TOPIK PER DISIPLIN (5 PILAR)", font=fonts["h3"], fill=NAVY)
    lx = 80
    lx = _pill(d4, lx, 548, "● RAME", (220, 252, 231), GREEN, fonts["micro_b"]) + 10
    lx = _pill(d4, lx, 548, "● SEDANG", (254, 243, 199), AMBER, fonts["micro_b"]) + 10
    _pill(d4, lx, 548, "● SEPI", (254, 226, 226), RED, fonts["micro_b"])

    disc_rows = tl.get("discipline_breakdown") or []
    cy_map = 584
    band_h = 100
    for drow in disc_rows[:5]:
        d_name = str(drow.get("discipline") or "kimia").lower()
        d_col = disc_colors.get(d_name, BLUE)
        cnt = int(drow.get("count") or 0)
        avg_v = float(drow.get("avg_views") or 0.0)
        bg_tint = (255, 251, 235) if cnt == 0 else (248, 250, 252)
        out_col = AMBER if cnt == 0 else BORDER
        d4.rounded_rectangle([76, cy_map, map_x1 - 20, cy_map + band_h], radius=12, fill=bg_tint, outline=out_col, width=2)
        d4.rounded_rectangle([76, cy_map, 84, cy_map + band_h], radius=4, fill=d_col)
        d4.text((96, cy_map + 10), d_name.upper(), font=fonts["small_b"], fill=d_col)
        d4.text((96, cy_map + 34), f"{cnt} video", font=fonts["micro_b"], fill=NAVY)
        d4.text((96, cy_map + 54), f"avg {avg_v:.0f}v", font=fonts["micro"], fill=SLATE)
        topics_in_d = drow.get("topics") or []
        if not topics_in_d:
            _pill(d4, 235, cy_map + 30, "BELUM ADA VIDEO • PELUANG BESAR MINGGU DEPAN!", (254, 243, 199), AMBER, fonts["micro_b"], pad_x=14, pad_y=7)
        else:
            col_chips = [226, 460]
            for c_i, t_item in enumerate(topics_in_d[:4]):
                tier = t_item.get("tier", "SEDANG")
                t_bg = (220, 252, 231) if tier == "RAME" else ((254, 226, 226) if tier == "SEPI" else (254, 243, 199))
                t_fg = GREEN if tier == "RAME" else (RED if tier == "SEPI" else AMBER)
                chip_txt = f"● {str(t_item.get('title') or '-')[:18]} ({int(t_item.get('views') or 0)})"
                px = col_chips[c_i % 2]
                py = cy_map + (14 if c_i < 2 else 54)
                _pill(d4, px, py, chip_txt, t_bg, t_fg, fonts["micro_b"], pad_x=8, pad_y=4)
        cy_map += band_h + 10

    # Middle Right: 2D Quadrant Bubble Plot + Section Proportion Bar
    qx0 = map_x1 + 20
    _card(d4, (qx0, 498, A4_W - 56, 1142))
    d4.text((qx0 + 22, 516), "PETA KUADRAN & SEGMEN", font=fonts["h3"], fill=PURPLE)
    gx0, gy0, gx1, gy1 = qx0 + 54, 566, A4_W - 82, 868
    mid_x = (gx0 + gx1) // 2
    mid_y = (gy0 + gy1) // 2
    # Quadrant background tints
    d4.rectangle([gx0, gy0, mid_x, mid_y], fill=(239, 246, 255))       # Top-left: POTENSI
    d4.rectangle([mid_x, gy0, gx1, mid_y], fill=(236, 253, 245))       # Top-right: UTAMA
    d4.rectangle([gx0, mid_y, mid_x, gy1], fill=(248, 250, 252))       # Bottom-left: GAP
    d4.rectangle([mid_x, mid_y, gx1, gy1], fill=(255, 247, 237))       # Bottom-right: EVALUASI
    d4.rectangle([gx0, gy0, gx1, gy1], outline=MUTED, width=2)
    d4.line([(mid_x, gy0), (mid_x, gy1)], fill=MUTED, width=2)
    d4.line([(gx0, mid_y), (gx1, mid_y)], fill=MUTED, width=2)
    d4.text((gx0 + 8, gy0 + 6), "POTENSI", font=fonts["micro_b"], fill=BLUE)
    d4.text((mid_x + 8, gy0 + 6), "UTAMA", font=fonts["micro_b"], fill=GREEN)
    d4.text((gx0 + 8, gy1 - 22), "GAP / SEDIKIT", font=fonts["micro_b"], fill=SLATE)
    d4.text((mid_x + 8, gy1 - 22), "EVALUASI HOOK", font=fonts["micro_b"], fill=AMBER)
    d4.text((qx0 + 16, gy0), "Views", font=fonts["micro"], fill=SLATE)
    d4.text((mid_x - 40, gy1 + 8), "Jml Video →", font=fonts["micro"], fill=SLATE)

    max_cnt_q = max(2, max((int(d.get("count") or 0) for d in disc_rows), default=2))
    max_avg_q = max(50.0, max((float(d.get("avg_views") or 0.0) for d in disc_rows), default=50.0))
    zero_slot = 0
    for idx_q, drow in enumerate(disc_rows[:5]):
        d_name = str(drow.get("discipline") or "kimia").lower()
        d_col = disc_colors.get(d_name, BLUE)
        cnt = int(drow.get("count") or 0)
        avg_v = float(drow.get("avg_views") or 0.0)
        px_q = gx0 + 34 + int((gx1 - gx0 - 68) * (cnt / max_cnt_q))
        py_q = gy1 - 36 - int((gy1 - gy0 - 76) * min(1.0, avg_v / max_avg_q))
        if cnt == 0:
            px_q = gx0 + 38 + zero_slot * 78
            py_q = gy1 - 46 - (zero_slot % 2) * 34
            zero_slot += 1
        rad = 11 + min(13, cnt * 3)
        d4.ellipse([px_q - rad, py_q - rad, px_q + rad, py_q + rad], fill=d_col, outline=WHITE, width=2)
        lbl_x = min(gx1 - 80, max(gx0 + 6, px_q - 26))
        lbl_y = (py_q + rad + 4) if py_q - rad - 18 < gy0 + 24 else (py_q - rad - 16)
        d4.text((lbl_x, lbl_y), f"{d_name[:4].upper()} ({cnt})", font=fonts["micro_b"], fill=NAVY)

    # Stacked Section Breakdown inside Right Card
    d4.text((qx0 + 22, 906), "DISTRIBUSI SEGMEN CHANNEL", font=fonts["small_b"], fill=NAVY)
    sec_rows = tl.get("section_breakdown") or []
    tot_sec = max(1, sum(int(s.get("count") or 0) for s in sec_rows))
    bar_x0, bar_w_s = qx0 + 22, (A4_W - 56) - (qx0 + 44)
    cur_bx = bar_x0
    for srow in sec_rows:
        s_cnt = int(srow.get("count") or 0)
        if s_cnt > 0:
            sw = max(24, int(bar_w_s * (s_cnt / tot_sec)))
            d4.rectangle([cur_bx, 938, min(bar_x0 + bar_w_s, cur_bx + sw), 966], fill=sec_colors.get(srow["section"], BLUE))
            cur_bx += sw
    sy_leg = 982
    for srow in sec_rows:
        sname = srow["section"]
        scol = sec_colors.get(sname, BLUE)
        d4.rounded_rectangle([qx0 + 22, sy_leg + 4, qx0 + 36, sy_leg + 18], radius=3, fill=scol)
        d4.text(
            (qx0 + 44, sy_leg),
            f"{sname}: {srow['count']} video (avg {float(srow.get('avg_views') or 0.0):.0f} views)",
            font=fonts["micro_b"],
            fill=NAVY,
        )
        sy_leg += 28

    # Bottom Card: Key Insights Why Top vs Flop
    _card(d4, (56, 1164, A4_W - 56, A4_H - 56))
    d4.text((82, 1184), "ANALISIS AGENT 8: KENAPA TOPIK RAME VS KENAPA SEPI", font=fonts["h2"], fill=NAVY)
    hl_items = list(getattr(audit, "topic_performance_highlights", None) or [])
    if not hl_items:
        best_title = top_list[0]["title"] if top_list else "-"
        best_v = int(top_list[0].get("views") or 0) if top_list else 0
        worst_title = bot_list[0]["title"] if bot_list else "-"
        worst_v = int(bot_list[0].get("views") or 0) if bot_list else 0
        hl_items = [
            f"Topik Paling Rame: '{best_title}' ({best_v:,} views) — didorong konsep mengejutkan & visual mekanistik langsung di detik awal.",
            f"Topik Paling Sepi: '{worst_title}' ({worst_v:,} views) — butuh hook miskonsepsi lebih tajam di 2 detik pertama & jam tayang optimal.",
            "Peluang Gap Disiplin: Disiplin Fisika, Matematika, dan slot Kimia Dapur 12:00 WIB berpotensi besar memperluas audiens baru minggu depan.",
        ]
    ty = 1234
    for idx_h, item in enumerate(hl_items[:5]):
        accent = GREEN if idx_h == 0 else (RED if idx_h == 1 else BLUE)
        wrapped_hl = _wrap(d4, item, fonts["body"], A4_W - 190, max_lines=3)
        bar_h_hl = max(32, len(wrapped_hl) * 25)
        d4.rounded_rectangle([82, ty + 2, 90, ty + bar_h_hl], radius=4, fill=accent)
        for ln in wrapped_hl:
            if ty > A4_H - 80:
                break
            d4.text((104, ty), ln, font=fonts["body"], fill=NAVY)
            ty += 25
        ty += 16

    # ======================== PAGE 5: NEXT-WEEK 7-DAY × 2-SLOT TOPIC ROADMAP & CATALOG ========================
    p5 = Image.new("RGB", (A4_W, A4_H), BG)
    d5 = ImageDraw.Draw(p5)
    d5.rectangle([0, 0, A4_W, 110], fill=NAVY)
    d5.text((56, 30), "NEXT WEEK TOPIC ROADMAP (7 DAYS × 2 GLOBAL SLOTS)", font=fonts["h2"], fill=YELLOW)
    d5.text(
        (56, 68),
        f"04:00 WIB (16:00 EST US Peak) • 21:00 WIB (09:00 EST EU/US Peak) • Mon-Fri EVERYDAY_TECH & Sat-Sun TECH_RADAR ({audit.week_ending})",
        font=fonts["small"],
        fill=(203, 213, 225),
    )

    days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_abbr = {"Mon": "MON", "Tue": "TUE", "Wed": "WED", "Thu": "THU", "Fri": "FRI", "Sat": "SAT", "Sun": "SUN"}
    by_day: dict[str, dict[str, dict[str, str]]] = {}
    for slot in getattr(audit, "next_week_topic_map", None) or []:
        d_low = str(getattr(slot, "day_label", "")).strip().lower()
        d_match = next((d for d in days_order if d.lower().startswith(d_low[:3])), None)
        if not d_match:
            continue
        t_slot = str(getattr(slot, "slot_time_wib", "21:00"))
        by_day.setdefault(d_match, {})[t_slot] = {
            "section": str(getattr(slot, "section", "TECH_RADAR" if d_match in ("Sat", "Sun") else "EVERYDAY_TECH")),
            "discipline": str(getattr(slot, "discipline", "gadgets")).lower(),
            "planned_topic": str(getattr(slot, "planned_topic", "-")),
            "rationale": str(getattr(slot, "rationale", "-")),
        }

    default_04 = [
        ("gadgets", "How Dead Phones Still Pay", "Universal NFC tap-to-pay mystery"),
        ("engineering", "Why Induction Stoves Stay Cool", "Magnetic eddy currents visual"),
        ("materials", "Why Nothing Sticks To Teflon", "Everyday pan paradox hook"),
        ("energy", "How EVs Refuel While Braking", "Regenerative braking explained"),
        ("biotech", "How Smartwatches Read Your Pulse", "Green PPG light blood shadow"),
        ("gadgets", "TECH RADAR: Solid-State Batteries", "Weekend breakthrough impact"),
        ("engineering", "TECH RADAR: Next-Gen Smart Glass", "Weekend urban tech spotlight"),
    ]
    default_21 = [
        ("gadgets", "Why Black Pixels Save OLED Battery", "Dark mode vs LCD truth"),
        ("engineering", "How Elevators Never Freefall", "1852 mechanical safety hook"),
        ("materials", "Why Phone Glass Survives Drops", "Potassium ion armor in glass"),
        ("energy", "Why Airplane Windows Have Pinholes", "Pressure safety mystery"),
        ("biotech", "Why Blue Light Tricks Your Brain", "2 AM phone sleep impact"),
        ("materials", "TECH RADAR: Self-Healing Concrete", "Weekend infrastructure tech"),
        ("energy", "TECH RADAR: Ultra-White Cooling Paint", "Weekend climate-tech radar"),
    ]
    all_slots_flat: list[dict[str, str]] = []
    for i_d, dname in enumerate(days_order):
        sec_def = "TECH_RADAR" if dname in ("Sat", "Sun") else "EVERYDAY_TECH"
        if "04:00" not in by_day.get(dname, {}):
            dd, dt, dr = default_04[i_d]
            by_day.setdefault(dname, {})["04:00"] = {"section": sec_def, "discipline": dd, "planned_topic": dt, "rationale": dr}
        if "21:00" not in by_day.get(dname, {}):
            ed, et, er = default_21[i_d]
            by_day.setdefault(dname, {})["21:00"] = {"section": sec_def, "discipline": ed, "planned_topic": et, "rationale": er}
        all_slots_flat.append(by_day[dname]["04:00"])
        all_slots_flat.append(by_day[dname]["21:00"])

    # Top Balance Strip
    _card(d5, (56, 130, A4_W - 56, 232))
    d5.text((82, 144), "14-SLOT WEEKLY BALANCE", font=fonts["h3"], fill=NAVY)
    _pill(d5, A4_W - 430, 140, "10× EVERYDAY_TECH  |  4× TECH_RADAR", (241, 245, 249), NAVY, fonts["micro_b"], pad_x=12, pad_y=5)
    px = 82
    for p_name in ("gadgets", "engineering", "materials", "energy", "biotech"):
        p_cnt = sum(1 for sl in all_slots_flat if sl["discipline"] == p_name)
        p_col = disc_colors.get(p_name, BLUE)
        px = _pill(d5, px, 184, f"{p_name.upper()}: {p_cnt} slot", p_col, WHITE, fonts["micro_b"], pad_x=12, pad_y=6) + 12

    # 7-Day Visual Matrix
    _card(d5, (56, 250, A4_W - 56, 1280))
    d5.text((178, 266), "04:00 WIB (16:00 EST — US PEAK)", font=fonts["small_b"], fill=BLUE)
    d5.text((688, 266), "21:00 WIB (09:00 EST — EU/US PEAK)", font=fonts["small_b"], fill=PURPLE)

    d5.line([(114, 326), (114, 1220)], fill=(203, 213, 225), width=4)
    ry = 298
    row_h = 128
    for dname in days_order:
        is_wknd = dname in ("Sat", "Sun")
        node_col = (234, 88, 12) if is_wknd else NAVY
        d5.rounded_rectangle([76, ry + 32, 152, ry + 92], radius=14, fill=node_col)
        d5.text((92, ry + 48), day_abbr[dname], font=fonts["body_b"], fill=YELLOW if is_wknd else WHITE)

        s04 = by_day[dname]["04:00"]
        is_r04 = s04["section"] == "TECH_RADAR"
        s04_col = (234, 88, 12) if is_r04 else BLUE
        s04_bg = (255, 247, 237) if is_r04 else (239, 246, 255)
        d5.rounded_rectangle([176, ry, 670, ry + row_h], radius=14, fill=s04_bg, outline=s04_col, width=2)
        px04 = _pill(d5, 190, ry + 10, f"04:00 {s04['section']}", s04_col, WHITE, fonts["micro_b"], pad_x=8, pad_y=3) + 8
        _pill(d5, px04, ry + 10, s04["discipline"].upper(), disc_colors.get(s04["discipline"], BLUE), WHITE, fonts["micro_b"], pad_x=8, pad_y=3)
        t04_lines = _wrap(d5, s04["planned_topic"], fonts["small_b"], 462, max_lines=2)
        ty04 = ry + 40
        for ln in t04_lines:
            d5.text((190, ty04), ln, font=fonts["small_b"], fill=NAVY)
            ty04 += 20
        r04_lines = _wrap(d5, f"→ {s04['rationale']}", fonts["micro"], 462, max_lines=2)
        d5.text((190, ry + 86), r04_lines[0], font=fonts["micro"], fill=SLATE)

        s21 = by_day[dname]["21:00"]
        is_r21 = s21["section"] == "TECH_RADAR"
        s21_col = (234, 88, 12) if is_r21 else PURPLE
        s21_bg = (255, 247, 237) if is_r21 else (245, 243, 255)
        d5.rounded_rectangle([688, ry, A4_W - 76, ry + row_h], radius=14, fill=s21_bg, outline=s21_col, width=2)
        px21 = _pill(d5, 702, ry + 10, f"21:00 {s21['section']}", s21_col, WHITE, fonts["micro_b"], pad_x=8, pad_y=3) + 8
        _pill(d5, px21, ry + 10, s21["discipline"].upper(), disc_colors.get(s21["discipline"], BLUE), WHITE, fonts["micro_b"], pad_x=8, pad_y=3)
        t21_lines = _wrap(d5, s21["planned_topic"], fonts["small_b"], 450, max_lines=2)
        ty21 = ry + 40
        for ln in t21_lines:
            d5.text((702, ty21), ln, font=fonts["small_b"], fill=NAVY)
            ty21 += 20
        r21_lines = _wrap(d5, f"→ {s21['rationale']}", fonts["micro"], 450, max_lines=2)
        d5.text((702, ry + 86), r21_lines[0], font=fonts["micro"], fill=SLATE)

        ry += row_h + 10

    # Bottom Catalog Card: Produced Science Topics Inventory
    _card(d5, (56, 1300, A4_W - 56, A4_H - 56))
    d5.text((82, 1318), f"ARSIP KATALOG TOPIK VIDEO YANG SUDAH DIBUAT ({tl.get('total_science_videos', 0)} VIDEO SAINS)", font=fonts["h3"], fill=NAVY)
    cat_topics = tl.get("all_topics") or []
    if cat_topics:
        cx_left, cx_right = 82, 636
        cy_cat = 1358
        for idx_c, ct in enumerate(cat_topics[:16]):
            cx = cx_left if idx_c < 8 else cx_right
            row_idx = idx_c % 8
            row_y = cy_cat + row_idx * 40
            tier = ct.get("tier", "SEPI")
            t_col = GREEN if tier == "RAME" else (AMBER if tier == "SEDANG" else RED)
            t_bg = (220, 252, 231) if tier == "RAME" else ((254, 243, 199) if tier == "SEDANG" else (254, 226, 226))
            _pill(d5, cx, row_y, tier[:4], t_bg, t_col, fonts["micro_b"], pad_x=8, pad_y=3)
            d5.text((cx + 66, row_y + 2), f"{str(ct.get('title') or '-')[:33]}", font=fonts["micro_b"], fill=NAVY)
            d5.text((cx + 370, row_y + 2), f"{str(ct.get('discipline') or 'kim')[:3].upper()} • {int(ct.get('views') or 0):,}v", font=fonts["micro"], fill=SLATE)
    else:
        d5.text((82, 1370), "Belum ada topik video sains tercatat.", font=fonts["body"], fill=SLATE)

    p1.save(out_pdf, "PDF", resolution=150.0, save_all=True, append_images=[p2, p3, p4, p5])
    return out_pdf
