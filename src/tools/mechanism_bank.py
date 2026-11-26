"""Self-expanding Everyday Sci-Tech Mechanistic Animation Bank for Bill_Explains.

Architecture:
1. 12 built-in 30fps Sci-Tech simulation engines rendered by `mechanistic_animator.py`:
   - electron_transfer, polymer_crosslink, molecular_reaction, receptor_binding, force_wave, math_geometry,
   - magnetic_induction, circuit_signal, optical_emission, phase_thermal, membrane_transport, crystal_lattice
2. A live JSON preset bank (`data/mechanism_bank.json`) queried by Agent 3 (script/storyboard) and
   automatically expanded every Sunday by Agent 8 (Weekly Growth Auditor) whenever new sci-tech
   topics or breakthroughs require fresh visual mechanisms.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BANK_PATH = BASE_DIR / "data" / "mechanism_bank.json"

BUILTIN_MECHANISM_ENGINES: dict[str, dict[str, str]] = {
    "magnetic_induction": {
        "name": "Magnetic Flux & Inductive Eddy Currents",
        "when_to_use": "Induction cooktops, NFC contactless payment, wireless Qi charging, EV regen motors, asphalt traffic loops.",
        "visual": "Copper coil windings pulsing glowing magnetic flux rings that induce swirling eddy currents in the target.",
    },
    "circuit_signal": {
        "name": "Microchip, Sensor Grid & Signal Routing",
        "when_to_use": "Capacitive touchscreens, Face ID IR dot arrays, microchip logic, smartphone ISP image processing, sensors.",
        "visual": "Silicon IC core pulsing live circuit traces and sensor grid nodes into decoded digital output.",
    },
    "optical_emission": {
        "name": "Photon Beam, LED Subpixels & Bandgap Optics",
        "when_to_use": "Smartwatch PPG green LEDs, OLED self-emissive black pixels, solar cell p-n junctions, radiative white-roof cooling.",
        "visual": "Photon/LED source emitting crisp animated light waves that excite electrons or reflect cleanly off targets.",
    },
    "phase_thermal": {
        "name": "Thermal Expansion, Heat Pump & Phase Change",
        "when_to_use": "Bridge metal expansion joints, fridge refrigerant heat cycles, air-fryer hot convection, microwave steam heating.",
        "visual": "Live digital thermometer paired with particles expanding, convecting, or exchanging latent heat.",
    },
    "membrane_transport": {
        "name": "Selective Filter Pores & Solid-Electrolyte Transport",
        "when_to_use": "Activated-carbon water filters, solid-state battery ceramic separators, airplane bleed-hole pressure equalization.",
        "visual": "Multi-layer porous barrier trapping large impurities/dendrites while letting target ions or pure water flow through.",
    },
    "crystal_lattice": {
        "name": "Atomic Lattice Armor & Stress Engineering",
        "when_to_use": "Gorilla Glass potassium-ion exchange, stainless steel self-healing chromium oxide, tempered car glass, metallic alloys.",
        "visual": "Ordered atomic grid locking larger reinforcing ions cleanly into surface compression pockets.",
    },
    "electron_transfer": {
        "name": "Electron Flow & Electrochemical Redox",
        "when_to_use": "Li-ion batteries, corona power-line discharge, electroplating, static electricity, galvanic corrosion.",
        "visual": "Donor and acceptor terminals exchanging glowing yellow electrons (e-) along curved energy arcs.",
    },
    "polymer_crosslink": {
        "name": "Polymer Chains & Viscoelastic Cross-Linking",
        "when_to_use": "Memory foam heat softening, cloudy bent plastic crazing, structural hydrogels, carbon fiber composites.",
        "visual": "Polymer node chains flexing under mechanical load then locking tight via crosslink bridges.",
    },
    "molecular_reaction": {
        "name": "Surface Chemistry & Molecular Shielding",
        "when_to_use": "Nonstick PTFE fluorine shielding, hydrophobic coatings, catalytic converters, Maillard crisping.",
        "visual": "Reactive molecular clusters colliding or forming protective surface repulsion bonds.",
    },
    "receptor_binding": {
        "name": "Biotech Biosensor & Nerve Receptor Lock-and-Key",
        "when_to_use": "Blue-light melanopsin eye receptors, VR vestibular-visual conflict, caffeine/biosensor docking.",
        "visual": "Signal molecule locking smoothly into a biological receptor pocket to fire a pulse.",
    },
    "force_wave": {
        "name": "Wave Interference, Acoustics & Mechanical Force",
        "when_to_use": "Active noise-canceling 180-deg anti-waves, microwave cavity standing nodes, elevator wedge brakes, wind aerodynamics.",
        "visual": "Traveling waveforms colliding or canceling cleanly alongside force vectors.",
    },
    "math_geometry": {
        "name": "Charging Curves, Thresholds & Scaling Laws",
        "when_to_use": "Fast-charging 0-80% slowdown curve, 3-blade turbine aerodynamic ratio, exponential sensor/compute scaling.",
        "visual": "Live coordinate plot drawing a real curve with a glowing tracer dot and limit threshold.",
    },
}

DEFAULT_CUSTOM_PRESETS: list[dict[str, Any]] = [
    {
        "preset_id": "nfc_inductive_coupling",
        "label_title": "13.56 MHZ INDUCTIVE POWER",
        "engine_kind": "magnetic_induction",
        "accent_color": "cyan",
        "trigger_keywords": ["nfc", "payment", "contactless", "card", "checkout", "tap", "dead phone"],
        "default_actor_a": "Reader Coil",
        "default_actor_b": "Phone NFC Coil",
        "default_result": "Chip Powered!",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "induction_eddy_current",
        "label_title": "MAGNETIC EDDY CURRENTS",
        "engine_kind": "magnetic_induction",
        "accent_color": "orange",
        "trigger_keywords": ["induction", "cooktop", "stove", "ferromagnetic", "eddy", "skillet", "pan"],
        "default_actor_a": "Copper Coil",
        "default_actor_b": "Steel Pan Base",
        "default_result": "Direct Pan Heat",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "oled_self_emissive_pixel",
        "label_title": "SELF-EMISSIVE OLED DIODES",
        "engine_kind": "optical_emission",
        "accent_color": "purple",
        "trigger_keywords": ["oled", "dark mode", "black pixel", "display", "backlight", "lcd"],
        "default_actor_a": "RGB OLED",
        "default_actor_b": "Black = 0V Off",
        "default_result": "Zero Power Used",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "anc_phase_cancellation",
        "label_title": "180° ANTI-SOUND WAVE",
        "engine_kind": "force_wave",
        "accent_color": "blue",
        "trigger_keywords": ["earbuds", "noise canceling", "anc", "anti-sound", "roar", "airplane"],
        "default_actor_a": "Cabin Roar",
        "default_actor_b": "Inverted Wave",
        "default_result": "Waves Cancel Out",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "gorilla_glass_ion_exchange",
        "label_title": "POTASSIUM ION ARMOR GRID",
        "engine_kind": "crystal_lattice",
        "accent_color": "cyan",
        "trigger_keywords": ["gorilla glass", "potassium", "sodium", "drop", "crack", "phone glass"],
        "default_actor_a": "Giant K+ Ions",
        "default_actor_b": "Glass Lattice",
        "default_result": "Compressed Armor",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "ppg_green_led_pulse",
        "label_title": "PPG OPTICAL PULSE SENSOR",
        "engine_kind": "optical_emission",
        "accent_color": "green",
        "trigger_keywords": ["smartwatch", "green light", "pulse", "heart", "blood", "ppg"],
        "default_actor_a": "530nm Green LED",
        "default_actor_b": "Capillary Blood",
        "default_result": "Live BPM Signal",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "solid_state_ion_transport",
        "label_title": "CERAMIC SOLID ELECTROLYTE",
        "engine_kind": "membrane_transport",
        "accent_color": "amber",
        "trigger_keywords": ["solid state", "lithium", "dendrite", "separator", "ceramic", "battery"],
        "default_actor_a": "Li+ Ions",
        "default_actor_b": "Ceramic Barrier",
        "default_result": "Safe Fast Charge",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "capacitive_touch_grid",
        "label_title": "CAPACITIVE SENSOR MATRIX",
        "engine_kind": "circuit_signal",
        "accent_color": "blue",
        "trigger_keywords": ["touchscreen", "gloves", "capacitive", "electric field", "face id", "dot"],
        "default_actor_a": "Human Field",
        "default_actor_b": "Sensor Grid",
        "default_result": "Tap Detected!",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
]


def _default_bank_payload() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "core_engines": BUILTIN_MECHANISM_ENGINES,
        "custom_presets": list(DEFAULT_CUSTOM_PRESETS),
    }


def load_mechanism_bank(bank_path: Path = BANK_PATH) -> dict[str, Any]:
    """Load `data/mechanism_bank.json`, initializing it cleanly if absent."""
    if not bank_path.exists():
        payload = _default_bank_payload()
        try:
            bank_path.parent.mkdir(parents=True, exist_ok=True)
            bank_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return payload
    try:
        data = json.loads(bank_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_bank_payload()
        data.setdefault("core_engines", BUILTIN_MECHANISM_ENGINES)
        data.setdefault("custom_presets", [])
        return data
    except Exception:
        return _default_bank_payload()


def format_mechanism_catalog_for_agent3(bank_path: Path = BANK_PATH) -> dict[str, Any]:
    """Compact bank digest passed to Agent 3 so scripts pick varied, accurate Sci-Tech engines."""
    bank = load_mechanism_bank(bank_path)
    engines = {
        k: v.get("when_to_use", "")
        for k, v in (bank.get("core_engines") or BUILTIN_MECHANISM_ENGINES).items()
    }
    presets = [
        {
            "preset_id": p.get("preset_id"),
            "engine_kind": p.get("engine_kind"),
            "label_title": p.get("label_title"),
            "trigger_keywords": p.get("trigger_keywords", [])[:6],
            "default_actors": f"{p.get('default_actor_a')} -> {p.get('default_actor_b')} -> {p.get('default_result')}",
        }
        for p in (bank.get("custom_presets") or [])[-24:]
        if p.get("preset_id") and p.get("engine_kind") in BUILTIN_MECHANISM_ENGINES
    ]
    return {"core_engines": engines, "custom_presets": presets}


def resolve_preset_style(preset_id: Optional[str], bank_path: Path = BANK_PATH) -> dict[str, Any]:
    """Return styling metadata (accent_color / defaults) for a matching preset_id."""
    if not preset_id:
        return {}
    pid = preset_id.strip().lower()
    bank = load_mechanism_bank(bank_path)
    for preset in bank.get("custom_presets") or []:
        if str(preset.get("preset_id") or "").strip().lower() == pid:
            return dict(preset)
    return {}


def append_mechanism_presets_from_audit(
    proposals: list[Any],
    week_ending: str,
    bank_path: Path = BANK_PATH,
) -> list[dict[str, Any]]:
    """Persist new Sci-Tech mechanism bank presets proposed by Agent 8 (deduplicated & validated)."""
    if not proposals:
        return []
    bank = load_mechanism_bank(bank_path)
    existing: list[dict[str, Any]] = list(bank.get("custom_presets") or [])
    by_id = {str(p.get("preset_id") or "").strip().lower(): p for p in existing if p.get("preset_id")}
    added: list[dict[str, Any]] = []

    for prop in proposals:
        raw = prop.model_dump() if hasattr(prop, "model_dump") else dict(prop)
        slug = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("preset_id") or "").strip().lower()).strip("_")
        engine_kind = str(raw.get("engine_kind") or "").strip()
        if not slug or engine_kind not in BUILTIN_MECHANISM_ENGINES:
            continue
        if slug in by_id:
            continue
        entry = {
            "preset_id": slug,
            "label_title": str(raw.get("label_title") or "SCI-TECH MECHANISM").strip()[:34].upper(),
            "engine_kind": engine_kind,
            "accent_color": str(raw.get("accent_color") or "cyan").strip().lower(),
            "trigger_keywords": [
                str(k).strip().lower()
                for k in (raw.get("trigger_keywords") or [])
                if str(k).strip()
            ][:8],
            "default_actor_a": str(raw.get("default_actor_a") or "Input").strip()[:16],
            "default_actor_b": str(raw.get("default_actor_b") or "Process").strip()[:16],
            "default_result": str(raw.get("default_result") or "Output").strip()[:22],
            "rationale": str(raw.get("rationale") or "").strip()[:140],
            "created_by": "agent8_weekly_audit",
            "created_week": week_ending,
        }
        existing.append(entry)
        by_id[slug] = entry
        added.append(entry)

    if added:
        bank["core_engines"] = BUILTIN_MECHANISM_ENGINES
        bank["custom_presets"] = existing[-80:]
        bank_path.parent.mkdir(parents=True, exist_ok=True)
        bank_path.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
