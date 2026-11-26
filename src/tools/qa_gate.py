"""Layer 0-Token Deterministic QA Gate & Pre-Render English-Only Linter.

1. `lint_agent3_storyboard(agent3)`: Deterministic check BEFORE TTS/FFmpeg that rejects:
   - Any leaked Indonesian UI/narration token (SIMULASI, MEKANISME, EFEK, HASIL, SOLUSI, MOLEKUL, GAYA, etc.)
   - Any raw placeholder strings (Molecule B, Molekul B, PLACEHOLDER, TODO, None, undefined)
   - Consecutive duplicate mechanism engines across scenes (step[i].kind != step[i-1].kind)
   - Domain-misaligned engines (e.g., molecular_reaction on non-bonding Teflon/aluminum interface)
   - Wrong channel handle (@bill_explains without 12) or 'Follow' CTA on YouTube narration
2. `run_gate0_checks(...)`: Fast FFmpeg/ffprobe technical checks BEFORE invoking multimodal LLM QA.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from src.state import Agent3Output, Gate0CheckResult

FORBIDDEN_INDONESIAN_TOKENS: tuple[str, ...] = (
    "SIMULASI",
    "MEKANISME",
    "EFEK",
    "HASIL",
    "SOLUSI",
    "MOLEKUL",
    "GAYA",
    "TEKANAN",
    "REAKSI",
    "IKATAN",
    "TAHAP",
    "TERJAWAB",
    "FAKTOR",
    "KARENA",
    "DENGAN",
    "UNTUK",
    "ADALAH",
    "DALAM",
    "KEPADA",
    "SEBAGAI",
)

FORBIDDEN_PLACEHOLDERS: tuple[str, ...] = (
    "MOLEKUL B",
    "MOLECULE B",
    "MOLECULE A",
    "MOLEKUL A",
    "PLACEHOLDER",
    "TODO",
    "UNDEFINED",
    "LOREM IPSUM",
)

STRICT_EXACT_PLACEHOLDERS: frozenset[str] = frozenset({"NONE", "NULL", "N/A", "INPUT", "OUTPUT", "PROCESS", "MECHANISM"})

ENGINE_DOMAIN_RULES: dict[str, str] = {
    "magnetic_induction": "Inductive coils, magnetic fields, eddy currents, wireless power, NFC, electromagnets",
    "circuit_signal": "ICs, capacitive grids, sensor arrays, digital logic, microcontrollers, electrical signaling",
    "optical_emission": "Light beams, photons, OLED/LED pixels, solar PV, refraction, PPG sensors, optics",
    "phase_thermal": "Heat transfer, expansion, vapor chambers, boiling, refrigeration, thermals",
    "membrane_transport": "Selective filtration pores, ion/proton transport across solid electrolytes or membranes",
    "crystal_lattice": "Atomic lattices, glass ion-exchange compression, semiconductors, alloys",
    "electron_transfer": "Electrochemical redox, anode/cathode batteries, static charge, corrosion",
    "polymer_crosslink": "Polymer curing, crosslinking resins/adhesives, elastic/composite chains",
    "molecular_reaction": "ONLY when real chemical bond breaking/forming reaction occurs between species",
    "receptor_binding": "Biosensors, lock-and-key enzyme/receptor/molecular docking",
    "force_wave": "Acoustic/sound waves, ANC interference, pressure waves, fluid aerodynamics, mechanical vibration",
    "math_geometry": "Geometric trusses, gear ratios, logarithmic/exponential scaling curves, load vectors",
    "concept_comparison": "Neutral safe side-by-side comparison of two real objects, states, or materials",
    "micro_structure_callout": "Neutral safe microscopic/surface topography zoom without inventing fake bonds",
    "three_step_flow": "Neutral safe sequential 3-stage process progression",
    "stat_highlight": "Neutral safe quantitative measurement/percentage highlight",
}


def lint_agent3_storyboard(agent3: Agent3Output) -> list[str]:
    """Return a list of deterministic lint errors; empty list means storyboard is clean."""
    errors: list[str] = []

    def _scan_text(field_name: str, text: str | None) -> None:
        raw = (text or "").strip()
        if not raw:
            return
        up = raw.upper()
        for tok in FORBIDDEN_INDONESIAN_TOKENS:
            if re.search(rf"(?<![A-Z0-9]){re.escape(tok)}(?![A-Z0-9])", up):
                errors.append(f"{field_name}: leaked Indonesian token '{tok}' in '{raw[:60]}'")
        for ph in FORBIDDEN_PLACEHOLDERS:
            if ph in up:
                errors.append(f"{field_name}: placeholder string '{ph}' found in '{raw[:60]}'")
        if up in STRICT_EXACT_PLACEHOLDERS:
            errors.append(f"{field_name}: generic placeholder value '{raw}' is forbidden")
        if re.search(r"@bill_explains(?![0-9])", raw, flags=re.IGNORECASE):
            errors.append(f"{field_name}: outdated handle '@bill_explains' (must be '@Bill_explains12')")

    _scan_text("title", agent3.title)
    _scan_text("caption_social", agent3.caption_social)
    for r_i, ref in enumerate(agent3.references or []):
        _scan_text(f"references[{r_i}]", ref)

    prev_engine: str | None = None
    for idx, sc in enumerate(agent3.scenes):
        _scan_text(f"{sc.scene_id}.narration", sc.narration)
        _scan_text(f"{sc.scene_id}.overlay_badge", sc.overlay_badge)
        if re.search(r"\bfollow\s+(?:@?bill|us|me|for\s+more)\b", sc.narration or "", flags=re.IGNORECASE):
            errors.append(f"{sc.scene_id}.narration: uses 'Follow' instead of YouTube 'Subscribe' CTA")
        if re.search(r"bill_explains", sc.narration or "", flags=re.IGNORECASE):
            errors.append(f"{sc.scene_id}.narration: contains literal underscore 'bill_explains' (must say 'Bill Explains')")

        if sc.mechanism is not None:
            m = sc.mechanism
            _scan_text(f"{sc.scene_id}.mechanism.title", m.title)
            _scan_text(f"{sc.scene_id}.mechanism.actor_a", m.actor_a)
            _scan_text(f"{sc.scene_id}.mechanism.actor_b", m.actor_b)
            _scan_text(f"{sc.scene_id}.mechanism.result", m.result)
            _scan_text(f"{sc.scene_id}.mechanism.caption", m.caption)

            if prev_engine is not None and m.kind == prev_engine:
                errors.append(
                    f"{sc.scene_id}.mechanism.kind='{m.kind}' repeats consecutive engine from previous scene"
                )
            prev_engine = m.kind

            # Catch non-bonding surface interfaces falsely using molecular_reaction (chemical bond engine)
            if m.kind == "molecular_reaction":
                blob = f"{sc.narration} {m.title} {m.actor_a} {m.actor_b} {m.result}".lower()
                if any(k in blob for k in ("no chem bond", "zero chem bond", "zero bond", "does not bond", "doesn't bond", "mechanical", "anchoring", "van der waals")):
                    errors.append(
                        f"{sc.scene_id}.mechanism.kind='molecular_reaction' contradicts non-bonding interface; "
                        "use 'micro_structure_callout' or 'concept_comparison' instead"
                    )
        else:
            prev_engine = None

        # Zero static outro filler in last scene narration
        if idx == len(agent3.scenes) - 1:
            if "thank you for watching" in (sc.narration or "").lower():
                errors.append(f"{sc.scene_id}.narration: remove slow 'thank you for watching' dead-air outro")

    total_words = sum(len((sc.narration or "").split()) for sc in agent3.scenes)
    if total_words < 70:
        errors.append(
            f"script too short ({total_words} words; must be 85-120 spoken English words across scenes so TTS hits >=25s cleanly)"
        )

    return errors


def run_gate0_checks(
    video_path: str | Path,
    expected_width: int,
    expected_height: int,
    min_duration_sec: float = 20.0,
    max_duration_sec: float = 55.0,
) -> Gate0CheckResult:
    p = Path(video_path)
    issues: list[str] = []

    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(p),
        ],
        text=True,
    )
    meta = json.loads(raw)
    stream = meta["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    dur = float(meta["format"]["duration"])

    if width != expected_width or height != expected_height:
        issues.append(f"Resolution mismatch: got {width}x{height}, expected {expected_width}x{expected_height}")
    if dur < min_duration_sec or dur > max_duration_sec:
        issues.append(f"Duration {dur:.1f}s outside target [{min_duration_sec}s, {max_duration_sec}s]")

    vol_proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(p),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    mean_match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", vol_proc.stderr)
    max_match = re.search(r"max_volume:\s*([-\d.]+)\s*dB", vol_proc.stderr)
    mean_db = float(mean_match.group(1)) if mean_match else -99.0
    max_db = float(max_match.group(1)) if max_match else -99.0

    if mean_db < -28.0:
        issues.append(f"Audio too quiet (mean {mean_db:.1f} dB)")
    if max_db > -0.1:
        issues.append(f"Audio clipping risk (max {max_db:.1f} dB)")

    blk_proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(p),
            "-vf",
            "blackdetect=d=0.3:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    black_total = sum(
        float(m.group(1))
        for m in re.finditer(r"black_duration:([\d.]+)", blk_proc.stderr)
    )
    black_ratio = round(black_total / dur, 4) if dur > 0 else 1.0
    if black_ratio > 0.05:
        issues.append(f"Excessive black frames detected ({black_ratio*100:.1f}% of video)")

    return Gate0CheckResult(
        passed=len(issues) == 0,
        duration_sec=round(dur, 2),
        width=width,
        height=height,
        mean_volume_db=round(mean_db, 2),
        max_volume_db=round(max_db, 2),
        black_frame_ratio=black_ratio,
        issues=issues,
    )
