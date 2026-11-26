"""Strict JSON state schemas for the 8-Agent Everyday Sci-Tech Pipeline.

Each agent only reads the fields it needs and outputs compact structured JSON
to avoid chat-history bloat and keep token usage minimal.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


Discipline = Literal["gadgets", "engineering", "materials", "energy", "biotech"]


class IdeaCandidate(BaseModel):
    id: str
    discipline: Discipline = "gadgets"
    source_mode: Literal["viral_trend", "idea_bank"] = "idea_bank"
    viral_source_url: Optional[str] = None
    title: str
    phenomenon: str
    hook_3s: str = Field(description="Opening 3-second hook in conversational English")
    core_explanation: str = Field(description="1-2 sentence plain-English sci-tech core")
    real_footage_feasibility: str = Field(description="Why real footage is easy to find")


class Agent1Output(BaseModel):
    mode_used: Literal["viral_trend", "hybrid", "idea_bank_fallback"] = "idea_bank_fallback"
    candidates: list[IdeaCandidate] = Field(min_length=3, max_length=5)


class IdeaScore(BaseModel):
    id: str
    hook_score: float = Field(ge=1, le=10)
    clarity_score: float = Field(ge=1, le=10)
    visual_realness_score: float = Field(ge=1, le=10)
    overall_score: float = Field(ge=1, le=10)
    short_note: str


class Agent2Output(BaseModel):
    scores: list[IdeaScore]
    winner_id: str
    passed: bool
    improvement_notes: list[str] = Field(
        max_length=4,
        description="Max 4 concrete editorial improvements for Agent 3",
    )


class MechanismVisual(BaseModel):
    kind: Literal[
        "magnetic_induction",
        "circuit_signal",
        "optical_emission",
        "phase_thermal",
        "membrane_transport",
        "crystal_lattice",
        "electron_transfer",
        "polymer_crosslink",
        "molecular_reaction",
        "receptor_binding",
        "force_wave",
        "math_geometry",
        "concept_comparison",
        "micro_structure_callout",
        "three_step_flow",
        "stat_highlight",
    ]
    title: str = Field(
        min_length=2,
        description="Short English simulation title (<= 28 chars), e.g. 'EDDY CURRENT COIL' or 'NONSTICK PORE LOCK'",
    )
    actor_a: str = Field(
        min_length=2,
        description="Specific component/species A label (max 14 chars), e.g. 'Copper Coil', 'Li+ Ion', 'Al Pores'",
    )
    actor_b: str = Field(
        min_length=2,
        description="Specific component/species B label (max 14 chars), e.g. 'Eddy Current', 'Cathode', 'Hot PTFE'",
    )
    result: str = Field(
        min_length=2,
        description="Accurate physical/tech outcome label (max 20 chars), e.g. 'Instant Heat', 'Mech Anchoring'",
    )
    caption: str = Field(
        default="",
        description="Concise English caption under the animation (max 48 chars)",
    )
    preset_id: Optional[str] = Field(
        default=None,
        description="Optional matching preset_id from data/mechanism_bank.json",
    )
    accent_color: Optional[str] = Field(
        default=None,
        description="Optional color theme hint (blue, green, orange, purple, cyan, red, amber)",
    )


class SceneSpec(BaseModel):
    scene_id: str
    narration: str = Field(description="Clean standard American English narration/subtitle text for this scene")
    visual_query: str = Field(description="Concrete 2-4 word English physical-object search query for REAL footage")
    fallback_queries: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(
        default_factory=list,
        description="Tokens to reject for this shot (e.g. 'logo', 'text', 'watermark', 'presentation', 'lecture', 'intro', 'slide')",
    )
    overlay_badge: Optional[str] = Field(
        default=None,
        description="Optional short English callout badge (no Indonesian tokens, e.g. '13.56 MHz • NFC Coil')",
    )
    mechanism: Optional[MechanismVisual] = Field(
        default=None,
        description="Structured English visual mechanistic diagram spec matching scientific facts",
    )
    sfx_cue: Optional[Literal["whoosh", "pop", "riser", "chime", "ding", "spark", "none"]] = "whoosh"
    motion: Literal["slow_zoom_in", "slow_zoom_out", "pan_right", "static"] = "slow_zoom_in"


class Agent3Output(BaseModel):
    episode_slug: str
    title: str
    caption_social: str
    hashtags: list[str]
    references: list[str] = Field(
        default_factory=list,
        description="1-3 concise verified sci-tech references (journal/institution, year, URL/DOI) for YouTube description",
    )
    requested_by: Optional[str] = None
    scenes: list[SceneSpec] = Field(min_length=4, max_length=8)


class SceneAsset(BaseModel):
    scene_id: str
    footage_path: str
    source: str
    source_url: str
    license: str
    duration_sec: float
    width: int
    height: int
    footage_tier: str = Field(
        default="tier_1_video",
        description="Visual waterfall tier used: tier_1_video | tier_2_photo_ken_burns | tier_3_ai_scene_ken_burns",
    )
    sfx_path: Optional[str] = None
    animation_sfx_path: Optional[str] = None
    notes: str = ""


class Agent6Output(BaseModel):
    assets: list[SceneAsset]
    bgm_path: Optional[str] = None


class SubtitleChunk(BaseModel):
    start: float
    end: float
    text: str


class TimelineSegment(BaseModel):
    scene_id: str
    start_sec: float
    end_sec: float
    duration_sec: float
    footage_path: str
    footage_start_offset: float = 0.0
    narration: str = ""
    overlay_badge: Optional[str] = None
    mechanism: Optional[MechanismVisual] = None
    sfx_path: Optional[str] = None
    animation_sfx_path: Optional[str] = None
    sfx_gain_db: float = -10.0
    motion: str = "slow_zoom_in"


class RenderPlan(BaseModel):
    version: int = 1
    resolution_mode: Literal["proxy_480p", "final_1080p"] = "proxy_480p"
    title: str = ""
    discipline: str = "gadgets"
    section_tag: str = "EVERYDAY_TECH"
    section_tagline: str = ""
    requested_by: Optional[str] = None
    use_mascot: bool = True
    references: list[str] = Field(default_factory=list)
    cover_thumbnail_path: Optional[str] = None
    voice_path: str
    total_duration_sec: float
    segments: list[TimelineSegment]
    subtitles: list[SubtitleChunk]
    output_video_path: str
    subtitle_font_size_ratio: float = 0.052
    bgm_gain_db: float = -24.0


class Gate0CheckResult(BaseModel):
    passed: bool
    duration_sec: float
    width: int
    height: int
    mean_volume_db: float
    max_volume_db: float
    black_frame_ratio: float
    issues: list[str] = Field(default_factory=list)


class Agent5AuditOutput(BaseModel):
    version_audited: int
    passed: bool
    overall_score: float = Field(ge=1, le=10)
    visual_relevance_score: float = Field(ge=1, le=10)
    subtitle_readability_score: float = Field(ge=1, le=10)
    pacing_and_audio_score: float = Field(ge=1, le=10)
    revision_target: Literal["none", "editor", "asset_scout", "both"] = "none"
    affected_scenes: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list, max_length=4)
    patch_instructions: list[str] = Field(default_factory=list, max_length=4)


class PlatformCopy(BaseModel):
    platform: Literal["youtube_shorts", "tiktok", "instagram_reels"]
    title: str = Field(description="Short punchy zero-spoiler title (3-6 words, < 70 chars)")
    caption: str = Field(description="Platform-optimized English caption with CTA")
    hashtags: list[str]
    pinned_comment: Optional[str] = Field(
        default=None,
        description="Lightweight conversational English comment starting with an easy 1-word debate/poll tied to the video topic plus a topic request prompt",
    )


class PlatformPublishResult(BaseModel):
    platform: Literal["youtube_shorts", "tiktok", "instagram_reels"]
    mode: Literal["dry_run", "draft", "public"]
    status: Literal["packaged_only", "uploaded_draft", "published", "skipped_missing_creds", "error"]
    post_id: Optional[str] = None
    post_url: Optional[str] = None
    detail: str = ""


class Agent7Output(BaseModel):
    thumbnail_headline_top: str
    thumbnail_headline_main: str
    thumbnail_badge: str
    thumbnail_path: Optional[str] = None
    platform_copies: list[PlatformCopy]
    publish_results: list[PlatformPublishResult] = Field(default_factory=list)


class EpisodeState(BaseModel):
    episode_id: str
    status: Literal[
        "init",
        "ideated",
        "audited_idea",
        "scripted",
        "assets_ready",
        "rendered_proxy",
        "qa_passed",
        "rendered_final",
        "uploaded",
        "published",
        "needs_human_review",
    ] = "init"
    revision_count: int = 0
    requested_by: Optional[str] = None
    request_id: Optional[str] = None
    request_source_comment: Optional[str] = None
    request_comment_id: Optional[str] = None
    agent1: Optional[Agent1Output] = None
    agent2: Optional[Agent2Output] = None
    agent3: Optional[Agent3Output] = None
    agent6: Optional[Agent6Output] = None
    render_plan: Optional[RenderPlan] = None
    gate0: Optional[Gate0CheckResult] = None
    agent5_history: list[Agent5AuditOutput] = Field(default_factory=list)
    agent7: Optional[Agent7Output] = None
    final_video_path: Optional[str] = None
    gdrive_folder_url: Optional[str] = None
    youtube_url: Optional[str] = None


class AudienceRequestItem(BaseModel):
    request_id: str = ""
    requester_name: str = Field(description="Commenter @handle or display name to mention when fulfilled (e.g. '@AlexTech')")
    source_comment: str = Field(description="Exact short excerpt of the audience comment requesting the topic or question")
    source_video_id: Optional[str] = None
    source_comment_id: Optional[str] = None
    topic_title: str = Field(description="Concise, engaging English everyday sci-tech Shorts topic fulfilling this viewer request")
    discipline: Discipline = "gadgets"
    status: Literal["pending", "published", "rejected"] = "pending"
    created_week: str = ""
    fulfilled_episode_id: Optional[str] = None
    fulfilled_youtube_url: Optional[str] = None


class NextWeekSlotPlan(BaseModel):
    day_label: str = Field(description="Day name: Mon, Tue, Wed, Thu, Fri, Sat, or Sun")
    slot_time_wib: Literal["04:00", "21:00"]
    section: Literal["EVERYDAY_TECH", "TECH_RADAR"]
    discipline: str = Field(description="One of: gadgets, engineering, materials, energy, biotech")
    planned_topic: str = Field(description="Concrete topic title/concept (< 75 chars) for next week")
    rationale: str = Field(description="Brief rationale (< 90 chars): e.g. double down on winner / viewer request / fill pillar gap")


class FlowClipPromptPlan(BaseModel):
    slot_label: str = Field(description="Target schedule, e.g. 'Tue 04:00 WIB (16:00 EST)' or 'Sat 21:00 WIB (TECH RADAR)'")
    topic_title: str = Field(description="Short episode topic title")
    suggested_filename: str = Field(description="Lowercase underscore MP4 filename for Agent 6 auto-match (e.g. induction_coil_glow.mp4)")
    why_flow_needed: str = Field(description="Brief reason (< 100 chars) why this scene benefits from Google Flow B-roll")
    flow_prompt_en: str = Field(description="Ready-to-copy Google Flow prompt (9:16 vertical, photorealistic macro/tech B-roll, no text/watermark)")
    recommended_model: str = Field(default="Omni Flash 1.2 (720p, ~18-24 cr)", description="Credit-efficient Google Flow model")
    estimated_credits: int = Field(default=20, description="Estimated Flow credits for 1 clip")


class NextWeekTopicMapOutput(BaseModel):
    next_week_topic_map: list[NextWeekSlotPlan] = Field(
        default_factory=list,
        description="Exactly 14-slot roadmap (Mon-Sun × 04:00 & 21:00 WIB): Mon-Fri EVERYDAY_TECH, Sat-Sun TECH_RADAR",
    )
    flow_clip_prompts: list[FlowClipPromptPlan] = Field(
        default_factory=list,
        description="Only 4-6 highest-impact scenes next week recommended for manual Google AI Flow generation",
    )


class MechanismBankPresetProposal(BaseModel):
    preset_id: str = Field(description="Lowercase snake_case unique preset ID, e.g. 'capacitive_touch_sensing'")
    label_title: str = Field(description="Concise uppercase English header (<= 28 chars), e.g. 'CAPACITIVE TOUCH GRID'")
    engine_kind: Literal[
        "magnetic_induction",
        "circuit_signal",
        "optical_emission",
        "phase_thermal",
        "membrane_transport",
        "crystal_lattice",
        "electron_transfer",
        "polymer_crosslink",
        "molecular_reaction",
        "receptor_binding",
        "force_wave",
        "math_geometry",
    ] = "circuit_signal"
    accent_color: Literal["blue", "green", "orange", "purple", "cyan", "red", "amber"] = "cyan"
    trigger_keywords: list[str] = Field(
        default_factory=list,
        description="3-6 English everyday sci-tech keywords triggering this preset",
    )
    default_actor_a: str = Field(default="Input", description="Short default actor A (<= 14 chars)")
    default_actor_b: str = Field(default="Mechanism", description="Short default actor B (<= 14 chars)")
    default_result: str = Field(default="Output", description="Short default result label (<= 20 chars)")
    rationale: str = Field(default="", description="Brief English reason why this preset expands the Sci-Tech bank")


class Agent8WeeklyAuditOutput(BaseModel):
    week_ending: str = Field(description="ISO date YYYY-MM-DD (WIB) of the Sunday audit")
    summary: str = Field(description="Executive summary in English of this week's channel & video performance")
    top_performing_patterns: list[str] = Field(
        default_factory=list,
        description="3-5 concrete patterns that worked best (topics, hooks, pillars, pacing, visuals)",
    )
    bottlenecks_and_weaknesses: list[str] = Field(
        default_factory=list,
        description="3-5 concrete weaknesses holding back views, retention, likes, or comments",
    )
    topic_performance_highlights: list[str] = Field(
        default_factory=list,
        description="3-5 findings comparing which produced topics got highest views/engagement vs lowest and why",
    )
    next_week_topic_map: list[NextWeekSlotPlan] = Field(
        default_factory=list,
        description="14-slot weekly roadmap (Mon-Sun × 04:00 & 21:00 WIB): Mon-Fri EVERYDAY_TECH, Sat-Sun TECH_RADAR",
    )
    flow_clip_prompts: list[FlowClipPromptPlan] = Field(
        default_factory=list,
        description="4-6 high-impact weekly prompts selected for manual Google AI Flow generation",
    )
    agent_1_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 1 (Trend Scout & Ideation) for next week",
    )
    agent_2_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 2 (Editorial & Hook Auditor) for next week",
    )
    agent_3_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 3 (Script, Hook & Mechanistic Storyboarder) for next week",
    )
    agent_4_animator_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete directives for Agent 3 & Agent 4 Animator to improve Sci-Tech mechanistic diagram accuracy & diversity",
    )
    agent_5_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 5 (Video QA Auditor) for next week",
    )
    agent_6_directives: list[str] = Field(
        default_factory=list,
        description="2-3 concrete instructions for Agent 6 (Footage Scout) to reduce wasted screening & find better clips faster",
    )
    mechanism_audit_summary: str = Field(
        default="",
        description="Critical evaluation of how accurately mechanistic animations matched this week's Sci-Tech topics vs generic repetition",
    )
    mechanism_mismatches: list[str] = Field(
        default_factory=list,
        description="Specific episodes/scenes where the mechanistic diagram engine felt mismatched or repetitive, plus fixes",
    )
    new_mechanism_presets: list[MechanismBankPresetProposal] = Field(
        default_factory=list,
        description="1-3 brand-new Sci-Tech preset proposals added automatically to data/mechanism_bank.json",
    )
    workflow_efficiency_summary: str = Field(
        default="",
        description="Assessment of weekly production speed, revision rate, topic retries, and Gemini/ElevenLabs token efficiency",
    )
    token_and_cost_optimizations: list[str] = Field(
        default_factory=list,
        description="2-4 concrete workflow/prompt/caching optimizations to save tokens, reduce failed revisions, and speed up production",
    )
    next_week_experiments: list[str] = Field(
        default_factory=list,
        description="2-3 concrete engagement experiments to test next week (hook style, CTA, pacing, topic angle)",
    )
    recommended_weekend_hours_wib: list[str] = Field(
        default_factory=lambda: ["04:00", "21:00"],
        description="Top 2 optimal HH:MM WIB upload times for Saturday/Sunday TECH_RADAR edition based on analytics",
    )
    tech_radar_weekend_directives: list[str] = Field(
        default_factory=list,
        description="2-3 concrete tech/science breakthrough themes & search angles for next weekend's TECH_RADAR edition",
    )
    audience_requests: list[AudienceRequestItem] = Field(
        default_factory=list,
        description="Up to 5 valid everyday sci-tech content/topic requests extracted from viewer comments, ready to produce next week",
    )
    audience_geo_insights: str = Field(
        default="",
        description="Assessment of top international countries (US, UK, CA, AU, IN, EU, etc.) and time-zone engagement trends",
    )
