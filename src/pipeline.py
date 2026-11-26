"""8-Agent Everyday Sci-Tech Content Automation Orchestrator.

Supports:
- Full automatic execution when API providers (gemini_api / claude_code_cli / anthropic_api / openrouter) are enabled.
- Antigravity Local trial mode (`antigravity_local`) where Antigravity runs each agent role via strict JSON handoffs
  in `episodes/<episode_id>/handoffs/`.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from src.brand_config import (
    get_brand_handle,
    get_brand_name,
    get_brand_spoken_name,
    get_daily_slots_wib,
    get_youtube_cta,
)
from src.memory import StudioMemory
from src.providers import call_structured_agent
from src.state import (
    Agent1Output,
    Agent2Output,
    Agent3Output,
    Agent5AuditOutput,
    Agent6Output,
    Agent7Output,
    EpisodeState,
    RenderPlan,
    TimelineSegment,
)
from src.tools.asset_scout import scout_assets_for_scenes
from src.tools.gdrive_sync import upload_episode_to_gdrive
from src.tools.mechanistic_animator import render_hybrid_science_video
from src.tools.qa_gate import lint_agent3_storyboard, run_gate0_checks
from src.tools.social_publisher import publish_to_all_platforms
from src.tools.thumbnail_maker import generate_cover_thumbnail
from src.tools.trend_scout import build_agent1_input_brief, get_blocked_topic_sets
from src.tools.tts_engine import generate_episode_voiceover

BASE_DIR = Path(__file__).resolve().parent.parent
WIB = ZoneInfo("Asia/Jakarta")


def load_dotenv(env_path: Optional[Path] = None) -> None:
    """Load key=value pairs from .env into os.environ without overwriting existing vars."""
    path = env_path or (BASE_DIR / ".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value


def load_configs() -> tuple[dict, dict]:
    brand = yaml.safe_load((BASE_DIR / "config" / "brand.yaml").read_text(encoding="utf-8"))
    agents = yaml.safe_load((BASE_DIR / "config" / "agents.yaml").read_text(encoding="utf-8"))
    return brand, agents


def episode_paths(episode_id: str) -> dict[str, Path]:
    ep_dir = BASE_DIR / "episodes" / episode_id
    handoffs = ep_dir / "handoffs"
    assets = ep_dir / "assets"
    renders = ep_dir / "renders"
    for d in (ep_dir, handoffs, assets, renders):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "ep_dir": ep_dir,
        "handoffs": handoffs,
        "assets": assets,
        "renders": renders,
        "state": ep_dir / "episode_state.json",
    }


def load_or_init_state(episode_id: str) -> EpisodeState:
    p = episode_paths(episode_id)["state"]
    if p.exists():
        return EpisodeState.model_validate_json(p.read_text(encoding="utf-8"))
    return EpisodeState(episode_id=episode_id)


def save_state(state: EpisodeState) -> Path:
    p = episode_paths(state.episode_id)["state"]
    p.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return p


def _verify_url_status_200(url: str) -> bool:
    import requests

    try:
        r = requests.head(url, timeout=4, allow_redirects=True, headers={"User-Agent": "BillExplainsStudio/2.0"})
        if r.status_code == 200:
            return True
        if r.status_code in (403, 405):
            r2 = requests.get(url, timeout=5, stream=True, allow_redirects=True, headers={"User-Agent": "BillExplainsStudio/2.0"})
            return r2.status_code == 200
    except Exception:
        return False
    return False


def _sanitize_verified_reference(ref_text: str) -> str:
    """Drop broken/hallucinated URLs/DOIs via HTTP 200 check while preserving clean source titles."""
    import re

    cleaned = " ".join((ref_text or "").strip().split())
    if not cleaned:
        return ""
    urls = re.findall(r"https?://[^\s,)>]+", cleaned)
    for u in urls:
        if not _verify_url_status_200(u):
            cleaned = cleaned.replace(u, "").strip(" -•|")
    doi_m = re.search(r"\b(?:doi:\s*|https?://doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", cleaned, flags=re.IGNORECASE)
    if doi_m and not any(doi_m.group(1) in u for u in urls):
        doi_url = f"https://doi.org/{doi_m.group(1)}"
        if _verify_url_status_200(doi_url):
            cleaned = f"{cleaned} ({doi_url})"
    return " ".join(cleaned.split())


def _collect_episode_references(state: EpisodeState) -> list[str]:
    """Assemble HTTP-verified sci-tech references + footage credits for the YouTube description."""
    refs: list[str] = []
    seen: set[str] = set()

    def add_ref(text: str, verify_links: bool = True) -> None:
        cleaned = _sanitize_verified_reference(text) if verify_links else " ".join(text.strip().split())
        if cleaned and len(cleaned) >= 6 and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            refs.append(cleaned)

    if state.agent3 and state.agent3.references:
        for r in state.agent3.references:
            add_ref(r, verify_links=True)

    if state.agent1 and state.agent2:
        winner = next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if winner and winner.viral_source_url:
            if _verify_url_status_200(winner.viral_source_url):
                add_ref(f"Research Source: {winner.viral_source_url}", verify_links=False)

    if not refs and state.agent3:
        add_ref(f"Engineering & Science Reference: {state.agent3.title}", verify_links=False)

    if state.agent6 and state.agent6.assets:
        sources = sorted({a.source.replace("_", " ").title() for a in state.agent6.assets})
        add_ref(f"Visual Media: {', '.join(sources)} (CC / Free / AI Licensed)", verify_links=False)

    return refs[:4]


def _is_weekend_tech_radar_mode() -> bool:
    """Weekends (Sat/Sun WIB) feature TECH_RADAR: latest science & technology breakthroughs."""
    if os.environ.get("FORCE_TECH_RADAR", "").lower() == "true":
        return True
    if os.environ.get("FORCE_EVERYDAY_TECH", "").lower() == "true":
        return False
    return datetime.now(WIB).weekday() in (5, 6)


def build_render_plan(
    state: EpisodeState,
    voice_path: str,
    scene_timings: list,
    subtitles: list,
    resolution_mode: str = "proxy_480p",
    version: int = 1,
    subtitle_font_size_ratio: float = 0.052,
    bgm_gain_db: float = -24.0,
    scene_offsets: Optional[dict[str, float]] = None,
) -> RenderPlan:
    assert state.agent3 is not None and state.agent6 is not None
    brand, _ = load_configs()
    paths = episode_paths(state.episode_id)
    asset_map = {a.scene_id: a for a in state.agent6.assets}
    scene_map = {s.scene_id: s for s in state.agent3.scenes}
    offsets = scene_offsets or {}

    try:
        from src.tools.telegram_notify import get_owner_runtime_overrides

        ro = get_owner_runtime_overrides()
    except Exception:
        ro = {}

    from src.tools.asset_scout import resolve_scene_sfx_cues

    memory = StudioMemory(BASE_DIR)
    force_trans_sfx = bool(ro.get("force_sfx_every_transition", True))
    force_anim_sfx = bool(ro.get("force_sfx_every_animation", True))
    base_sfx_db = float(brand.get("audio_mix", {}).get("sfx_gain_db", -10.5))
    sfx_gain_db = round(max(-18.0, min(-4.0, base_sfx_db + float(ro.get("sfx_gain_db_delta") or 0.0))), 2)
    eff_bgm_gain_db = round(max(-34.0, min(-14.0, float(bgm_gain_db) + float(ro.get("bgm_gain_db_delta") or 0.0))), 2)
    eff_sub_ratio = round(max(0.038, min(0.068, float(subtitle_font_size_ratio) + float(ro.get("subtitle_scale_delta") or 0.0))), 4)

    winner_disc = "gadgets"
    if state.agent1 and state.agent2:
        winner = next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if winner:
            winner_disc = winner.discipline

    segments: list[TimelineSegment] = []
    total_segs = len(scene_timings)
    for seg_i, timing in enumerate(scene_timings):
        sc = scene_map[timing.scene_id]
        ast = asset_map[timing.scene_id]
        res_trans, res_anim = resolve_scene_sfx_cues(
            scene_idx=seg_i,
            total_scenes=total_segs,
            scene=sc,
            memory=memory,
            force_transition=force_trans_sfx,
            force_animation=force_anim_sfx,
        )
        final_trans_sfx = ast.sfx_path if (ast.sfx_path and Path(ast.sfx_path).exists()) else res_trans
        final_anim_sfx = (
            ast.animation_sfx_path
            if (getattr(ast, "animation_sfx_path", None) and Path(str(ast.animation_sfx_path)).exists())
            else res_anim
        )
        default_off = (
            round(min(1.2, max(0.0, ast.duration_sec - timing.duration_sec - 0.5)), 2)
            if seg_i == 0 and ast.duration_sec > timing.duration_sec + 1.5
            else 0.0
        )
        segments.append(
            TimelineSegment(
                scene_id=timing.scene_id,
                start_sec=timing.start_sec,
                end_sec=timing.end_sec,
                duration_sec=timing.duration_sec,
                footage_path=ast.footage_path,
                footage_start_offset=offsets.get(timing.scene_id, default_off),
                narration=sc.narration,
                overlay_badge=sc.overlay_badge,
                mechanism=sc.mechanism,
                sfx_path=final_trans_sfx,
                animation_sfx_path=final_anim_sfx,
                sfx_gain_db=sfx_gain_db,
                motion=sc.motion,
            )
        )

    if resolution_mode == "final_1080p":
        out_mp4 = paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    else:
        out_mp4 = paths["renders"] / f"proxy_v{version}_480p.mp4"

    is_radar = _is_weekend_tech_radar_mode()
    section_tag = "TECH_RADAR" if is_radar else "EVERYDAY_TECH"
    section_tagline = (
        "TECH RADAR — Latest Sci-Tech Breakthroughs"
        if is_radar
        else "EVERYDAY TECH — Science Around Us"
    )

    total_dur = segments[-1].end_sec if segments else 0.0
    use_mascot = bool(brand.get("video", {}).get("mascot", {}).get("enabled", True))
    return RenderPlan(
        version=version,
        resolution_mode=resolution_mode,
        title=state.agent3.title,
        discipline=winner_disc,
        section_tag=section_tag,
        section_tagline=section_tagline,
        requested_by=state.requested_by or getattr(state.agent3, "requested_by", None),
        use_mascot=use_mascot,
        references=_collect_episode_references(state),
        voice_path=voice_path,
        total_duration_sec=round(total_dur, 3),
        segments=segments,
        subtitles=subtitles,
        output_video_path=str(out_mp4),
        subtitle_font_size_ratio=eff_sub_ratio,
        bgm_gain_db=eff_bgm_gain_db,
    )


def run_agent_1_to_3(
    episode_id: str,
    topic_hint: Optional[str] = None,
    avoid_titles: Optional[list[str]] = None,
    requested_by: Optional[str] = None,
    request_id: Optional[str] = None,
    request_source_comment: Optional[str] = None,
    request_comment_id: Optional[str] = None,
) -> EpisodeState:
    brand, agents_cfg = load_configs()
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if requested_by:
        state.requested_by = requested_by
    if request_id:
        state.request_id = request_id
    if request_source_comment:
        state.request_source_comment = request_source_comment
    if request_comment_id:
        state.request_comment_id = request_comment_id

    brand_name = get_brand_name()
    brand_handle = get_brand_handle()
    policy = brand["brand"]["ideation_policy"]
    is_radar = _is_weekend_tech_radar_mode()
    trend_brief = build_agent1_input_brief(
        BASE_DIR,
        lookback_hours=int(policy.get("viral_lookback_hours", 72)),
        min_viral_matches=int(policy.get("min_viral_matches", 2)),
        is_tech_radar=is_radar,
    )

    from src.tools.mechanism_bank import format_mechanism_catalog_for_agent3
    from src.tools.weekly_growth_auditor import load_latest_agent_directives
    from src.tools.telegram_notify import (
        get_agent_specific_owner_directives,
        get_owner_runtime_overrides,
    )

    weekly_dir = load_latest_agent_directives()
    owner_ro = get_owner_runtime_overrides()
    owner_a1 = get_agent_specific_owner_directives("agent_1")
    owner_a2 = get_agent_specific_owner_directives("agent_2")
    owner_a3 = get_agent_specific_owner_directives("agent_3")
    owner_a4 = get_agent_specific_owner_directives("agent_4")
    animator_dir = owner_a4 + (weekly_dir.get("agent_4_animator_directives") or [])
    mechanism_catalog = format_mechanism_catalog_for_agent3()
    radar_dir = weekly_dir.get("tech_radar_weekend_directives", []) if is_radar else []
    slot_map_hint: list[str] = []
    try:
        day_abbr = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][datetime.now(WIB).weekday()]
        t_pub = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
        slots = get_daily_slots_wib()
        slot_time = t_pub if t_pub in slots else (slots[0] if datetime.now(WIB).hour < 12 else slots[1])
        for row in weekly_dir.get("next_week_topic_map") or []:
            if str(row.get("day_label", "")).lower().startswith(day_abbr.lower()) and str(row.get("slot_time_wib", "")) == slot_time:
                slot_map_hint.append(
                    f"AGENT 8 TOPIC ROADMAP FOR THIS SLOT ({day_abbr} {slot_time} WIB / {row.get('section', '')}): "
                    f"Prioritize '{row.get('planned_topic')}' (pillar={row.get('discipline')}; rationale={row.get('rationale')})."
                )
                break
    except Exception:
        slot_map_hint = []

    if agents_cfg["runtime_mode"] == "antigravity_local":
        raise RuntimeError(
            "Pipeline is in 'antigravity_local' mode. Switch `runtime_mode: production` in config/agents.yaml "
            "or write handoff JSONs directly."
        )

    radar_instruction = (
        "WEEKEND EDITION 'TECH RADAR' ACTIVE: Prioritize fresh breakthroughs in `viral_candidates` "
        "(new battery tech, materials, AI hardware, energy, medical/wearable sensors, urban engineering) and "
        "translate them directly into what changes in ordinary people's everyday lives! "
        if is_radar
        else (
            "WEEKDAY EDITION 'EVERYDAY TECH' ACTIVE: Focus strictly on applied science and everyday technology "
            "around us (phones, OLED screens, NFC, earbuds, microwaves, induction stoves, bridges, EVs, smartwatches, glass, plastics) "
            "— NOT abstract pure textbook science! "
        )
    )
    req_a1_note = (
        f"PRIORITY VIEWER REQUEST FROM '{requested_by}': At least candidate #1 MUST directly answer '{topic_hint}' "
        f"(viewer comment: '{request_source_comment or topic_hint}')! "
        if requested_by and topic_hint
        else ""
    )

    blocked_titles, _, _ = get_blocked_topic_sets(BASE_DIR)
    merged_avoid = list(dict.fromkeys([*(avoid_titles or []), *blocked_titles]))

    # Agent 1: Ideation (5 everyday sci-tech ideas + 0-3s hook)
    a1_cfg = agents_cfg["agents"]["agent_1_ideation"]
    state.agent1 = call_structured_agent(
        provider=a1_cfg["provider"],
        model=a1_cfg.get("model", a1_cfg["recommended_prod_model"]),
        system_prompt=(
            f"You are Agent 1 (Viral Everyday Sci-Tech Trend Scout & Ideation) for '{brand_name}' ({brand_handle}), "
            "an international English-language YouTube Shorts channel. Generate 5 scroll-stopping everyday science & technology "
            "video candidates across our 5 pillars: gadgets, engineering, materials, energy, biotech.\n"
            f"{req_a1_note}{radar_instruction}"
            "Follow `trend_brief.global_viral_style_benchmarks` (Veritasium, Cleo Abram, Zack D. Films, Branch Education, MKBHD Shorts):\n"
            "- ZERO-SPOILER PUNCHY TITLE: Title MUST be **3 to 6 English words**, creating a sharp curiosity gap "
            "(e.g., 'How Dead Phones Still Pay', 'Why Fast Charging Hits A Wall', 'Why Nothing Sticks To Teflon') — "
            "NEVER spoil the answer inside the title or write dry textbook headlines!\n"
            "PRIORITY 1: When `viral_candidates` has fresh everyday-relatable tech/science news, adapt them (`source_mode: 'viral_trend'`).\n"
            "PRIORITY 2 (Fallback): Select and sharpen unused ideas from `idea_bank_fallback_candidates` (`source_mode: 'idea_bank'`).\n"
            "Every candidate must be visualizable with REAL, concrete, family-safe everyday/tech/lab footage (never full animation), "
            "MUST NOT repeat ANY topic or closely matching concept in `avoid_titles`, and MUST apply any `weekly_growth_directives`."
        ),
        user_payload={
            "brand": brand["brand"],
            "section": "TECH_RADAR (Weekend Latest Sci-Tech)" if is_radar else "EVERYDAY_TECH (Daily Tech Around Us)",
            "trend_brief": trend_brief,
            "topic_hint": topic_hint,
            "requested_by": requested_by,
            "request_comment": request_source_comment,
            "avoid_titles": merged_avoid[:80],
            "weekly_growth_directives": owner_a1 + weekly_dir.get("agent_1_directives", []) + radar_dir + slot_map_hint,
        },
        response_model=Agent1Output,
        handoff_path=paths["handoffs"] / "agent1_ideas.json",
    )
    state.status = "ideated"
    save_state(state)

    # Agent 2: Editorial & Retention Auditor
    a2_cfg = agents_cfg["agents"]["agent_2_editorial_auditor"]
    req_a2_note = (
        f"Because viewer '{requested_by}' requested '{topic_hint}', ensure the candidate answering that request wins cleanly! "
        if requested_by and topic_hint
        else ""
    )
    state.agent2 = call_structured_agent(
        provider=a2_cfg["provider"],
        model=a2_cfg.get("model", a2_cfg["recommended_prod_model"]),
        system_prompt=(
            f"You are Agent 2 (Global Shorts Hook & Editorial Auditor) for {brand_name}. Score all candidates strictly on:\n"
            "(1) EVERYDAY RELATABILITY FOR GLOBAL AUDIENCES: Will someone scrolling Shorts in the US/UK/worldwide immediately recognize "
            "this object or daily tech annoyance ('Wait, I use that every day—how DOES that work?')?\n"
            "(2) ZERO-SPOILER 3-6 WORD TITLE & 0-3s CURIOSITY HOOK: Penalize titles that give away the mechanism! "
            "Reward mystery titles and hooks that open with a bold bet, contrast, or everyday puzzle "
            "(ZERO slow greetings, ZERO 'Have you ever wondered...' clichés, ZERO dictionary definitions).\n"
            "(3) CONCRETE VISUAL CONTRAST & NO DUPLICATES: Reject any candidate that overlaps `avoid_titles`.\n"
            "Pick the #1 winner and give max 4 sharp improvement notes (rewriting title to <=6 spoiler-free English words if needed). "
            f"{req_a2_note}"
        ),
        user_payload={
            "candidates": [c.model_dump() for c in state.agent1.candidates],
            "avoid_titles": merged_avoid[:80],
            "requested_by": requested_by,
            "topic_hint": topic_hint,
            "weekly_growth_directives": owner_a2 + weekly_dir.get("agent_2_directives", []) + radar_dir,
        },
        response_model=Agent2Output,
        handoff_path=paths["handoffs"] / "agent2_audit.json",
    )
    state.status = "audited_idea"
    save_state(state)

    # Agent 3: Script, Shot-List & Mechanistic Storyboard Reviser
    a3_cfg = agents_cfg["agents"]["agent_3_script_reviser"]
    winner = next(
        (c for c in state.agent1.candidates if c.id == state.agent2.winner_id),
        state.agent1.candidates[0],
    )
    if winner.source_mode == "idea_bank":
        memory.mark_idea_used(winner.id, episode_id)

    spoken_brand = get_brand_spoken_name()
    youtube_cta = get_youtube_cta()
    radar_a3_note = (
        "WEEKEND 'TECH RADAR' EPISODE (~34-46s TOTAL): Do NOT open with 'Welcome to Tech Radar'! "
        "Open scene_1 (0-3s) IMMEDIATELY with an everyday pain point or bold prediction, weave in the new breakthrough "
        "naturally in scene 2-3 (naming the university/lab/company & mechanism), and end cleanly right after the aha moment. "
        "In `references`, cite verifiable journal/institution sources. "
        if is_radar
        else "WEEKDAY 'EVERYDAY TECH' EPISODE (~30-44s TOTAL): Keep every line grounded in real devices/homes/streets people touch daily, "
        "and end crisply right after the 'aha!' payoff (zero slow outro). "
    )
    custom_dur = owner_ro.get("target_duration_sec")
    if isinstance(custom_dur, list) and len(custom_dur) == 2:
        lo_d, hi_d = max(28, int(custom_dur[0])), min(46, int(custom_dur[1]))
        lo_w, hi_w = int(lo_d * 2.4), int(hi_d * 2.5)
        duration_rule = f"~{lo_d}-{hi_d}s spoken (~{lo_w}-{hi_w} English words across 5-6 tight scenes)"
    else:
        duration_rule = (
            "~34-46s spoken (~85-115 English words across 5-6 tight scenes, ~15-21 words per scene)"
            if is_radar
            else "~30-44s spoken (~75-108 English words across 5-6 tight scenes, ~14-19 words per scene)"
        )
    spoken_requester = requested_by.lstrip("@") if requested_by else ""
    req_a3_note = (
        f"VIEWER REQUEST FROM '{requested_by}' (comment: '{request_source_comment or topic_hint}'): "
        f"In scene_1, give a quick shoutout right beside the hook ('Answering {spoken_requester}'s question!'), "
        f"and in the closing line thank {spoken_requester} briefly! "
        if requested_by
        else ""
    )

    a3_system_prompt = (
        f"You are Agent 3 (English Script, Shot-List & Mechanistic Storyboard Reviser) for {spoken_brand}. "
        f"Write a crisp, high-retention conversational American English script ({duration_rule}) "
        "applying Agent 2's notes and `weekly_growth_directives`.\n"
        "STRICT ENGLISH-ONLY RULE: 100% American English across EVERY field (narration, title, overlay_badge, mechanism labels). "
        "ZERO Indonesian words (never use SIMULASI, MEKANISME, EFEK, HASIL, SOLUSI, MOLEKUL, GAYA, REAKSI, IKATAN, TAHAP). "
        "ZERO generic placeholders ('Input', 'Output', 'Process', 'Mechanism', 'Molecule B').\n"
        "SPEECH & BRAND CTA HYGIENE:\n"
        f"- In spoken `narration`, ALWAYS write '{spoken_brand}' (ZERO underscores, ZERO '@' symbols!). Never use 'Follow' on YouTube.\n"
        "- TITLE RULE: `title` MUST be 3 to 6 punchy English words WITHOUT spoiling the answer!\n"
        f"{req_a3_note}{radar_a3_note}"
        "SHORTS RETENTION STRUCTURE (STRICT 30-46s TARGET):\n"
        "- Scene 1 (0-3s Hook — ZERO GREETING, ZERO SPOILER): Start directly with a surprising contrast, everyday frustration, "
        "or a 1-second visual bet ('Wait—how does a dead phone still pay at checkout?'). NEVER start with 'Hey guys' or 'Have you ever wondered'.\n"
        "- Middle scenes (Reveal -> Hidden Mechanism -> Twist): Crisp re-hook ('Here is the weird twist...') explaining the real mechanism.\n"
        f"- Final scene (Payoff — ZERO DEAD-AIR OUTRO): End cleanly RIGHT after the aha moment! Never say 'Thank you for watching'. "
        f"If adding a quick <=1.5s CTA, say '{youtube_cta}' + optional 1-word poll.\n"
        "VISUAL SHOT QUERY RULES (3-TIER WATERFALL):\n"
        "- Every scene's `visual_query` MUST be a concrete 2 to 4 word English phrase naming a real filmable physical object/action "
        "(e.g. 'nonstick frying pan egg', 'contactless card tap phone', 'smartwatch green optical sensor'). Never abstract concepts!\n"
        "- Provide 2-3 equally concrete `fallback_queries` and populate `negative_keywords` (`['logo', 'text', 'watermark', 'presentation', 'lecture', 'intro', 'slide']`).\n"
        "MECHANISTIC DIAGRAM & SCIENTIFIC ACCURACY RULES:\n"
        "- For ALL middle explanation scenes (scenes 2 through second-to-last), ALWAYS provide BOTH `overlay_badge` AND `mechanism`.\n"
        "- Choose `mechanism.kind` strictly matching the domain (NO consecutive duplicate engines: scene[i].kind != scene[i-1].kind!):\n"
        "  12 Core Engines: 'magnetic_induction', 'circuit_signal', 'optical_emission', 'phase_thermal', "
        "'membrane_transport', 'crystal_lattice', 'electron_transfer', 'polymer_crosslink', "
        "'molecular_reaction' (ONLY when real covalent/chemical bond forms or breaks — NEVER for non-bonding interfaces!), "
        "'receptor_binding', 'force_wave', 'math_geometry'.\n"
        "  4 Safe Neutral Templates (PREFER THESE whenever no specialized physics/bonding engine matches accurately): "
        "'concept_comparison', 'micro_structure_callout', 'three_step_flow', 'stat_highlight'.\n"
        "- Keep concise English `title` (<=28 chars), specific `actor_a` & `actor_b` (<=14 chars), accurate `result` (<=20 chars), and `caption` (<=48 chars).\n"
        "- Always set copyright-free `sfx_cue` (whoosh/pop/riser/spark/ding/chime) and populate `references` with 1-3 real verifiable engineering/science sources (for description only)."
    )

    from pydantic import BaseModel, Field

    class _FactCheckVerdict(BaseModel):
        passed: bool
        issues: list[str] = Field(default_factory=list, description="Exact scientific contradictions, acronym typos, or engine mismatches")

    fix_feedback: list[str] = []
    for a3_pass in range(2):
        user_payload_a3 = {
            "winner_idea": winner.model_dump(),
            "audit_notes": state.agent2.improvement_notes,
            "voice_style": brand["voice"],
            "requested_by": requested_by,
            "request_comment": request_source_comment,
            "mechanism_bank_catalog": mechanism_catalog,
            "animator_directives": animator_dir,
            "weekly_growth_directives": owner_a3 + weekly_dir.get("agent_3_directives", []) + radar_dir,
        }
        if fix_feedback:
            user_payload_a3["CRITICAL_REWRITE_FIXES_REQUIRED"] = fix_feedback

        cand_a3 = call_structured_agent(
            provider=a3_cfg["provider"],
            model=a3_cfg.get("model", a3_cfg["recommended_prod_model"]),
            system_prompt=a3_system_prompt,
            user_payload=user_payload_a3,
            response_model=Agent3Output,
            handoff_path=paths["handoffs"] / "agent3_script.json",
        )

        # Gate 1: Deterministic English-only + placeholder + duplicate-engine linter (0 tokens)
        lint_errs = lint_agent3_storyboard(cand_a3)

        # Gate 2: Lightweight Pre-Render Science & Acronym Fact-Check LLM Pass (only when lint clean or final pass)
        fc_errs: list[str] = []
        if not lint_errs:
            try:
                fc = call_structured_agent(
                    provider=a3_cfg["provider"],
                    model=agents_cfg.get("agents", {}).get("agent_6_asset_scout", {}).get("recommended_prod_model", "gemini-3.5-flash-lite"),
                    system_prompt=(
                        "You are a Science Communication Fact-Check Gate verifying an English Sci-Tech Shorts script + diagram storyboard BEFORE rendering.\n"
                        "Check strictly:\n"
                        "1. Zero contradiction between narration and diagram labels (e.g., never label non-bonding interfaces like PTFE+aluminum as 'BOND FORMED' or 'REACTION'; never claim carbon-fluorine bonds are weak!).\n"
                        "2. Exact spelling of all chemical/tech acronyms & terminology (PTFE not PTFS, Li-ion, PPG, LED, OLED, photoplethysmography, etc.).\n"
                        "3. Every step's selected `mechanism.kind` logically fits the physics/chemistry described (no consecutive identical `kind`, prefer neutral templates like `micro_structure_callout`/`concept_comparison` if unsure)."
                    ),
                    user_payload=cand_a3.model_dump(),
                    response_model=_FactCheckVerdict,
                    handoff_path=paths["handoffs"] / f"fact_check_p{a3_pass + 1}.json",
                )
                if not fc.passed and fc.issues:
                    fc_errs = list(fc.issues)
            except Exception as fc_exc:
                print(f"[fact_check] Warning: skipped optional LLM fact-check ({fc_exc})")

        all_errs = lint_errs + fc_errs
        if not all_errs:
            state.agent3 = cand_a3
            break

        print(f"[pipeline] Storyboard QA gate pass {a3_pass + 1} flagged {len(all_errs)} issue(s): {all_errs}")
        if a3_pass == 0:
            fix_feedback = all_errs
            continue
        if lint_errs:
            raise ValueError(f"Agent 3 storyboard failed deterministic English/placeholder linter: {lint_errs}")
        state.agent3 = cand_a3

    assert state.agent3 is not None
    if requested_by and not state.agent3.requested_by:
        state.agent3.requested_by = requested_by
    state.status = "scripted"
    save_state(state)
    return state


def run_agent_6_scout(episode_id: str) -> EpisodeState:
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None:
        raise RuntimeError("Run Agent 1-3 first before scouting assets.")

    handoff_a6 = paths["handoffs"] / "agent6_assets.json"
    if handoff_a6.exists():
        state.agent6 = Agent6Output.model_validate_json(handoff_a6.read_text(encoding="utf-8"))
    else:
        tried_urls: set[str] = set()
        state.agent6 = scout_assets_for_scenes(
            scenes=state.agent3.scenes,
            assets_dir=paths["assets"],
            memory=memory,
            tried_urls_out=tried_urls,
        )
        handoff_a6.write_text(state.agent6.model_dump_json(indent=2), encoding="utf-8")
        if tried_urls:
            (paths["handoffs"] / "rejected_urls.json").write_text(
                json.dumps(sorted(tried_urls), indent=2), encoding="utf-8"
            )

    state.status = "assets_ready"
    save_state(state)
    return state


def rescout_affected_scenes(episode_id: str, affected_scenes: list[str]) -> EpisodeState:
    """Replace only the scenes flagged by Agent 5 QA with fresh, alternative real footage."""
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.agent6 is None:
        return state

    target_ids = {s for s in affected_scenes if s}
    if not target_ids:
        return state

    rejected_file = paths["handoffs"] / "rejected_urls.json"
    prev_rejected: set[str] = set()
    if rejected_file.exists():
        try:
            prev_rejected = set(json.loads(rejected_file.read_text(encoding="utf-8")))
        except Exception:
            prev_rejected = set()

    existing_map = {a.scene_id: a for a in state.agent6.assets}
    exclude_urls = prev_rejected | {a.source_url for a in state.agent6.assets}
    tried_urls: set[str] = set(exclude_urls)

    state.agent6 = scout_assets_for_scenes(
        scenes=state.agent3.scenes,
        assets_dir=paths["assets"],
        memory=memory,
        exclude_urls=exclude_urls,
        existing_assets=existing_map,
        target_scene_ids=target_ids,
        tried_urls_out=tried_urls,
    )
    rejected_file.write_text(json.dumps(sorted(tried_urls), indent=2), encoding="utf-8")
    (paths["handoffs"] / "agent6_assets.json").write_text(
        state.agent6.model_dump_json(indent=2), encoding="utf-8"
    )
    save_state(state)
    return state


def run_agent_4_proxy_and_gate0(
    episode_id: str,
    version: int = 1,
    subtitle_font_size_ratio: float = 0.052,
    bgm_gain_db: float = -24.0,
    scene_offsets: Optional[dict[str, float]] = None,
) -> tuple[EpisodeState, str, str]:
    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.agent6 is None:
        raise RuntimeError("Need Agent 3 script and Agent 6 assets before rendering.")

    if (
        version > 1
        and state.render_plan is not None
        and Path(state.render_plan.voice_path).exists()
        and len(state.render_plan.segments) == len(state.agent3.scenes)
    ):
        from src.tools.tts_engine import SceneVoiceResult

        voice_path = state.render_plan.voice_path
        scene_timings = [
            SceneVoiceResult(
                scene_id=seg.scene_id,
                audio_path=voice_path,
                start_sec=seg.start_sec,
                end_sec=seg.end_sec,
                duration_sec=seg.duration_sec,
                subtitles=[],
            )
            for seg in state.render_plan.segments
        ]
        subtitles = list(state.render_plan.subtitles)
        print(f"[tts] Reusing voiceover audio from v1 for revision v{version} (saving ElevenLabs credits)")
    else:
        voice_cfg = dict(brand["voice"])
        try:
            import re as _re
            from src.tools.telegram_notify import get_owner_runtime_overrides

            spd_delta = int(get_owner_runtime_overrides().get("voice_speed_delta_pct") or 0)
            if spd_delta != 0:
                m_r = _re.search(r"([+-]?\d+)", str(voice_cfg.get("rate", "+0%")))
                cur_r = int(m_r.group(1)) if m_r else 0
                new_r = max(-20, min(25, cur_r + spd_delta))
                voice_cfg["rate"] = f"{new_r:+d}%"
        except Exception:
            pass
        voice_path, scene_timings, subtitles, _, tts_engine = generate_episode_voiceover(
            scenes=state.agent3.scenes,
            out_dir=paths["renders"] / f"audio_v{version}",
            voice=voice_cfg["voice_name"],
            rate=voice_cfg["rate"],
            pitch=voice_cfg["pitch"],
        )
        (paths["handoffs"] / "tts_meta.json").write_text(
            json.dumps({"tts_engine": tts_engine, "version": version}, indent=2),
            encoding="utf-8",
        )

    plan = build_render_plan(
        state=state,
        voice_path=voice_path,
        scene_timings=scene_timings,
        subtitles=subtitles,
        resolution_mode="proxy_480p",
        version=version,
        subtitle_font_size_ratio=subtitle_font_size_ratio,
        bgm_gain_db=bgm_gain_db,
        scene_offsets=scene_offsets,
    )
    use_mascot = bool(brand.get("video", {}).get("mascot", {}).get("enabled", True))
    video_path, sheet_path = render_hybrid_science_video(
        plan=plan,
        work_dir=paths["renders"],
        output_mp4=Path(plan.output_video_path),
        bgm_path=state.agent6.bgm_path,
        mascots_dir=BASE_DIR / "assets" / "mascots",
        use_mascot=use_mascot,
    )
    gate0 = run_gate0_checks(video_path, expected_width=480, expected_height=854)

    state.render_plan = plan
    state.gate0 = gate0
    state.status = "rendered_proxy"
    save_state(state)
    return state, video_path, sheet_path


def run_agent_5_video_qa(
    episode_id: str,
    sheet_path: str,
    version: int = 1,
) -> tuple[EpisodeState, Agent5AuditOutput]:
    """Strict multimodal Video QA Audit inspecting the rendered contact sheet + Gate 0 report."""
    brand, agents_cfg = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.render_plan is None or state.gate0 is None:
        raise RuntimeError("Render proxy and Gate 0 before running Agent 5 QA.")

    min_qa = float(brand.get("guardrails", {}).get("min_qa_score", 7.5))
    a5_cfg = agents_cfg["agents"]["agent_5_video_qa"]
    brand_name = get_brand_name()

    audit = call_structured_agent(
        provider=a5_cfg["provider"],
        model=a5_cfg.get("model", a5_cfg["recommended_prod_model"]),
        system_prompt=(
            f"You are Agent 5 (Strict Multimodal Video QA Auditor) for {brand_name} ({get_brand_handle()}). "
            "Inspect the attached contact sheet image carefully alongside Gate 0 technical checks and script. "
            "Be objective and strict — check EVERY item:\n"
            "0. SAFETY FIRST: Any nudity, suggestive, violent or inappropriate frame for a family-friendly tech/science "
            "channel = automatic fail with revision_target 'asset_scout' and that scene in affected_scenes.\n"
            "1. Full-screen 9:16 framing & clarity: Real video, photo-Ken-Burns, or AI 9:16 scene fills the 9:16 vertical frame cleanly "
            "without tiny boxed footage over static blur, watermarks, or off-topic visuals.\n"
            "2. Mechanistic overlays & English-only check: Animated mechanism diagrams are sharp with ZERO clipped text, ZERO Indonesian "
            "tokens (SIMULASI, EFEK, HASIL, MOLEKUL, REAKSI), ZERO placeholder strings (Molecule B), and handle displays @Bill_explains12.\n"
            "3. Duo mascots: Tech-geek mascots sit cleanly in the safe corner without blocking subtitles or central diagrams.\n"
            "4. Subtitle readability: English subtitles are crisp, centered, and inside the vertical Shorts safe zone.\n"
            "5. Final CTA ending: Confirm the LAST frame keeps active visual/mascot energy with a compact Subscribe badge and ZERO static 'THANK YOU FOR WATCHING' or 'SOURCES & REFERENCES' card.\n"
            "6. Gate 0 audio/video health: Must pass technical checks.\n"
            "7. Hook & Retention Pacing: Confirm scene_1 delivers an immediate scroll-stopping curiosity hook.\n"
            f"Set `passed: true` ONLY if Gate 0 passed AND overall_score >= {min_qa} AND no major visual/language defects exist. "
            "If failing, specify exact `revision_target` ('asset_scout', 'editor', or 'both'), `affected_scenes`, and up to 4 sharp `patch_instructions`."
        ),
        user_payload={
            "version_audited": version,
            "min_qa_score": min_qa,
            "gate0": state.gate0.model_dump(),
            "title": state.agent3.title,
            "references": state.render_plan.references,
            "weekly_growth_directives": (
                __import__("src.tools.telegram_notify", fromlist=["get_agent_specific_owner_directives"])
                .get_agent_specific_owner_directives("agent_5")
                + __import__("src.tools.weekly_growth_auditor", fromlist=["load_latest_agent_directives"])
                .load_latest_agent_directives()
                .get("agent_5_directives", [])
            ),
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_query": s.visual_query,
                    "overlay_badge": s.overlay_badge,
                }
                for s in state.agent3.scenes
            ],
        },
        response_model=Agent5AuditOutput,
        handoff_path=paths["handoffs"] / f"agent5_audit_v{version}.json",
        image_paths=[Path(sheet_path)],
    )

    if not state.gate0.passed or audit.overall_score < min_qa:
        audit.passed = False

    state.agent5_history.append(audit)
    state.revision_count = max(0, version - 1)
    if audit.passed:
        state.status = "qa_passed"
    save_state(state)
    return state, audit


def run_final_hd_render(episode_id: str) -> tuple[EpisodeState, str, str]:
    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.render_plan is None or state.agent3 is None or state.agent6 is None:
        raise RuntimeError("Render proxy and pass QA before final HD render.")

    final_plan = state.render_plan.model_copy(deep=True)
    final_plan.resolution_mode = "final_1080p"
    if state.agent7 and state.agent7.thumbnail_path and Path(state.agent7.thumbnail_path).exists():
        final_plan.cover_thumbnail_path = state.agent7.thumbnail_path
    final_plan.output_video_path = str(
        paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    )
    use_mascot = bool(brand.get("video", {}).get("mascot", {}).get("enabled", True))
    video_path, sheet_path = render_hybrid_science_video(
        plan=final_plan,
        work_dir=paths["renders"],
        output_mp4=Path(final_plan.output_video_path),
        bgm_path=state.agent6.bgm_path,
        mascots_dir=BASE_DIR / "assets" / "mascots",
        use_mascot=use_mascot,
    )
    gate0_hd = run_gate0_checks(video_path, expected_width=1080, expected_height=1920)
    state.render_plan = final_plan
    state.gate0 = gate0_hd
    state.final_video_path = video_path
    state.status = "rendered_final"
    save_state(state)

    memory = StudioMemory(BASE_DIR)
    last_score = state.agent5_history[-1].overall_score if state.agent5_history else None
    winner_cand = (
        next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if state.agent1 and state.agent2
        else None
    )
    winner_phenom = winner_cand.phenomenon if winner_cand else state.agent3.title
    winner_disc = winner_cand.discipline if winner_cand else "gadgets"
    memory.record_episode(
        episode_id=episode_id,
        slug=state.agent3.episode_slug,
        title=state.agent3.title,
        phenomenon=winner_phenom,
        status=state.status,
        discipline=winner_disc,
        final_score=last_score,
    )
    return state, video_path, sheet_path


def run_upload(
    episode_id: str,
    allow_youtube: bool = True,
    qa_summary: str = "",
    topic_attempts_used: int = 1,
    production_seconds: Optional[float] = None,
) -> EpisodeState:
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    url = upload_episode_to_gdrive(
        paths["ep_dir"],
        episode_name=episode_id,
        allow_youtube=allow_youtube,
        qa_summary=qa_summary,
    )
    state.gdrive_folder_url = url
    yt_meta = paths["handoffs"] / "youtube_upload.json"
    yt_privacy = ""
    if yt_meta.exists():
        try:
            yt_data = json.loads(yt_meta.read_text(encoding="utf-8"))
            state.youtube_url = yt_data.get("youtube_url")
            yt_privacy = str(yt_data.get("privacy_status") or "").lower()
        except Exception:
            pass
    if allow_youtube:
        state.status = "uploaded" if yt_privacy == "public" else "awaiting_review"
    save_state(state)

    if allow_youtube and state.agent3 and yt_privacy == "public":
        from src.tools.weekly_growth_auditor import record_published_episode

        mechanisms_used = [
            {
                "scene_id": sc.scene_id,
                "kind": sc.mechanism.kind,
                "preset_id": sc.mechanism.preset_id,
                "accent_color": sc.mechanism.accent_color,
                "title": sc.mechanism.title,
                "actor_a": sc.mechanism.actor_a,
                "actor_b": sc.mechanism.actor_b,
                "result": sc.mechanism.result,
            }
            for sc in state.agent3.scenes
            if sc.mechanism is not None
        ]
        record_published_episode(
            episode_id=episode_id,
            title=state.agent3.title,
            discipline=state.render_plan.discipline if state.render_plan else "gadgets",
            section=state.render_plan.section_tag if state.render_plan else "EVERYDAY_TECH",
            youtube_url=state.youtube_url,
            gdrive_folder_url=state.gdrive_folder_url,
            qa_score=state.agent5_history[-1].overall_score if state.agent5_history else None,
            duration_sec=state.render_plan.total_duration_sec if state.render_plan else None,
            hook_line=state.agent3.scenes[0].narration if state.agent3.scenes else "",
            revisions_used=state.revision_count,
            topic_attempts_used=topic_attempts_used,
            production_seconds=production_seconds,
            mechanisms_used=mechanisms_used,
        )

    memory = StudioMemory(BASE_DIR)
    last_score = state.agent5_history[-1].overall_score if state.agent5_history else None
    if state.agent3:
        memory.record_episode(
            episode_id=episode_id,
            slug=state.agent3.episode_slug,
            title=state.agent3.title,
            phenomenon=state.agent3.title,
            status="uploaded" if yt_privacy == "public" else "awaiting_review",
            final_score=last_score,
            gdrive_url=url,
        )
    return state


def prepare_episode_thumbnail(episode_id: str) -> EpisodeState:
    """Run Agent 7 copy generation (if needed) and generate 9:16 cover thumbnail BEFORE final 1080p render."""
    brand, agents_cfg = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None:
        raise RuntimeError("Need Agent 3 script before preparing Agent 7 thumbnail.")

    brand_handle = get_brand_handle()

    if state.agent7 is None:
        a7_cfg = agents_cfg["agents"]["agent_7_publisher"]
        req_a7_note = (
            f"IMPORTANT — VIEWER REQUEST SHOUTOUT: This episode fulfills a comment request from '{state.requested_by}' "
            f"(comment: '{state.request_source_comment or ''}'). Mention '{state.requested_by}' clearly inside BOTH `caption` AND `pinned_comment` "
            f"thanking them, then ask a super easy 1-word binary poll tied to this topic!"
            if state.requested_by
            else (
                "COMMENT-BOOSTER PINNED COMMENT RULE: NEVER write generic 'Did you know this?'. "
                "Both `caption` (closing line) AND `pinned_comment` MUST pose ONE playful, specific 1-word debate or poll "
                "tied directly to THIS video's everyday tech topic (e.g. 'Team Dark Mode or Light Mode? Comment DARK or LIGHT! 👇'), "
                "followed briefly by: 'What everyday tech should we decode next?' Keep `pinned_comment` under 200 chars."
            )
        )
        from src.tools.telegram_notify import get_agent_specific_owner_directives

        owner_a7 = get_agent_specific_owner_directives("agent_7")
        state.agent7 = call_structured_agent(
            provider=a7_cfg["provider"],
            model=a7_cfg.get("model", a7_cfg["recommended_prod_model"]),
            system_prompt=(
                f"You are Agent 7 (High-CTR Shorts Packaging & Social Media Publisher) for {get_brand_spoken_name()} ({brand_handle}). "
                "Create viral, non-spoiler English thumbnail copy AND platform-tailored title, punchy caption, "
                "hashtags, and a conversation-starting `pinned_comment` for every platform, strictly respecting any `owner_directives`.\n"
                "PLATFORM TITLE RULE: `platform_copies[].title` MUST be **3 to 6 punchy words** (<=45 chars) with ZERO spoiler.\n"
                f"YOUTUBE CTA RULE: On `youtube_shorts`, ALWAYS end the caption CTA with `{get_youtube_cta()}` ({brand_handle}). "
                "NEVER use the word 'Follow' in any YouTube title, caption, or pinned comment.\n"
                "REFERENCES RULE: Append `Sources:` followed by compact verified reference links/titles from `script.references` at the bottom of the YouTube Shorts caption.\n"
                "COVER THUMBNAIL COPY RULE: Keep `thumbnail_badge` to 1-2 uppercase words (<=16 chars, e.g. 'EVERYDAY TECH', 'TECH RADAR'), "
                "`thumbnail_headline_top` to 1-3 short words (<=15 chars, e.g. 'DEAD PHONE'), and `thumbnail_headline_main` to **2-3 explosive curiosity words** "
                "(<=14 chars, e.g. 'STILL PAYS?!', 'HIDDEN TRICK!', 'WHY 80%?!') that spark instant curiosity without spoiling! "
                f"{req_a7_note}"
            ),
            user_payload={
                "brand": brand["brand"],
                "youtube_cta": get_youtube_cta(),
                "script": state.agent3.model_dump(),
                "requested_by": state.requested_by,
                "request_source_comment": state.request_source_comment,
                "owner_directives": owner_a7,
                "platforms": brand.get("publishing", {}).get(
                    "platforms", ["youtube_shorts"]
                ),
            },
            response_model=Agent7Output,
            handoff_path=paths["handoffs"] / "agent7_package.json",
        )

    pkg = state.agent7
    # Deterministic safety scrub on YouTube copies: replace @bill_explains or 'Follow ' CTA
    for pc in pkg.platform_copies:
        if pc.platform == "youtube_shorts":
            pc.caption = pc.caption.replace("@bill_explains", brand_handle)
            pc.pinned_comment = pc.pinned_comment.replace("@bill_explains", brand_handle)
            if "follow " in pc.caption.lower() and "subscribe" not in pc.caption.lower():
                pc.caption = f"{pc.caption.rstrip()}\n\n{get_youtube_cta()} ({brand_handle})"

    thumb_path = paths["ep_dir"] / f"THUMBNAIL_{state.agent3.episode_slug}_9x16.jpg"
    if not pkg.thumbnail_path or not Path(pkg.thumbnail_path).exists():
        if state.render_plan and state.render_plan.segments:
            bg_clip = Path(state.render_plan.segments[0].footage_path)
            bg_ts = state.render_plan.segments[0].footage_start_offset + 1.4
        elif state.agent6 and state.agent6.assets:
            bg_clip = Path(state.agent6.assets[0].footage_path)
            bg_ts = 1.6
        else:
            raise RuntimeError("Need footage clip to generate cover thumbnail.")
        use_mascot = brand.get("video", {}).get("mascot", {}).get("enabled", True)
        generate_cover_thumbnail(
            bg_video_path=bg_clip,
            bg_timestamp_sec=bg_ts,
            headline_top=pkg.thumbnail_headline_top,
            headline_main=pkg.thumbnail_headline_main,
            badge_text=pkg.thumbnail_badge,
            out_jpg=thumb_path,
            mascots_dir=BASE_DIR / "assets" / "mascots",
            use_mascot=use_mascot,
        )
        pkg.thumbnail_path = str(thumb_path)

    (paths["handoffs"] / "agent7_package.json").write_text(
        pkg.model_dump_json(indent=2), encoding="utf-8"
    )
    save_state(state)
    return state


def run_agent_7_publish(
    episode_id: str,
    mode: Optional[str] = None,
    video_override: Optional[str] = None,
) -> EpisodeState:
    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = prepare_episode_thumbnail(episode_id)
    assert state.agent3 is not None and state.agent7 is not None
    pkg = state.agent7

    publish_mode = mode or os.environ.get(
        "PUBLISH_MODE",
        brand.get("publishing", {}).get("default_mode", "draft"),
    )
    if os.environ.get("ALLOW_DIRECT_PUBLIC", "false").lower() == "false" and publish_mode == "public":
        publish_mode = "draft"

    if video_override:
        final_video = Path(video_override)
    elif state.final_video_path and Path(state.final_video_path).exists():
        final_video = Path(state.final_video_path)
    else:
        candidates = sorted(paths["ep_dir"].glob("FINAL_*.mp4"))
        if not candidates:
            raise RuntimeError(f"No FINAL_*.mp4 found in {paths['ep_dir']}")
        final_video = candidates[-1]

    pkg.publish_results = publish_to_all_platforms(
        video_path=final_video,
        agent7_pkg=pkg,
        mode=publish_mode,  # type: ignore[arg-type]
    )
    (paths["handoffs"] / "agent7_package.json").write_text(
        pkg.model_dump_json(indent=2), encoding="utf-8"
    )
    state.agent7 = pkg
    if any(r.status in ("published", "uploaded_draft") for r in pkg.publish_results):
        state.status = "published" if publish_mode == "public" else "awaiting_review"
    save_state(state)
    return state


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=f"{get_brand_name()} 8-Agent Automation Studio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scout-trends")

    for name in ("ideate", "scout", "render-proxy", "render-final", "upload", "publish"):
        p = sub.add_parser(name)
        p.add_argument("--episode-id", required=True)
        if name == "ideate":
            p.add_argument("--topic-hint", default=None)
        if name == "render-proxy":
            p.add_argument("--version", type=int, default=1)
            p.add_argument("--bgm-gain-db", type=float, default=-24.0)
            p.add_argument("--font-ratio", type=float, default=0.052)
        if name == "publish":
            p.add_argument("--mode", choices=["dry_run", "draft", "public"], default=None)
            p.add_argument("--video", default=None)

    args = parser.parse_args()
    if args.cmd == "scout-trends":
        brief = build_agent1_input_brief(BASE_DIR)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    elif args.cmd == "ideate":
        st = run_agent_1_to_3(args.episode_id, topic_hint=args.topic_hint)
        print(json.dumps({"status": st.status, "title": st.agent3.title if st.agent3 else None}, indent=2))
    elif args.cmd == "scout":
        st = run_agent_6_scout(args.episode_id)
        print(json.dumps({"status": st.status, "assets": len(st.agent6.assets) if st.agent6 else 0}, indent=2))
    elif args.cmd == "render-proxy":
        st, vpath, spath = run_agent_4_proxy_and_gate0(
            args.episode_id,
            version=args.version,
            subtitle_font_size_ratio=args.font_ratio,
            bgm_gain_db=args.bgm_gain_db,
        )
        print(json.dumps({"video": vpath, "contact_sheet": spath, "gate0": st.gate0.model_dump() if st.gate0 else None}, indent=2))
    elif args.cmd == "render-final":
        st, vpath, spath = run_final_hd_render(args.episode_id)
        print(json.dumps({"final_video": vpath, "contact_sheet": spath, "gate0": st.gate0.model_dump() if st.gate0 else None}, indent=2))
    elif args.cmd == "upload":
        st = run_upload(args.episode_id)
        print(json.dumps({"status": st.status, "gdrive_url": st.gdrive_folder_url}, indent=2))
    elif args.cmd == "publish":
        st = run_agent_7_publish(args.episode_id, mode=args.mode, video_override=args.video)
        print(json.dumps(st.agent7.model_dump() if st.agent7 else {}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
