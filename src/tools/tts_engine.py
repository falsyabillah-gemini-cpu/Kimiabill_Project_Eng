"""English male voiceover engine (ElevenLabs primary with automatic quota-limit fallback to Edge-TTS)
with TTS Pronunciation Normalizer (tts_text vs subtitle_text separation), <=0.15s gap trimming,
and zero dead-air ending for crisp YouTube Shorts pacing.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import edge_tts
import requests

from src.brand_config import BASE_DIR, get_brand_spoken_name
from src.state import SceneSpec, SubtitleChunk

DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam (clear, energetic English male voice)
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_EDGE_VOICE = "en-US-AndrewNeural"
PRONUNCIATION_DICT_PATH = BASE_DIR / "config" / "pronunciation_dict.json"


class ElevenLabsQuotaOrAuthError(RuntimeError):
    """Raised when ElevenLabs hits quota/limit/auth error so entire episode switches cleanly to Edge-TTS."""


@dataclass
class SceneVoiceResult:
    scene_id: str
    audio_path: str
    start_sec: float
    end_sec: float
    duration_sec: float
    subtitles: list[SubtitleChunk]


def _load_pronunciation_dict() -> dict[str, Any]:
    if not PRONUNCIATION_DICT_PATH.exists():
        return {}
    try:
        return json.loads(PRONUNCIATION_DICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_for_tts(text: str) -> str:
    """Convert standard American English script text (`subtitle_text`) into clean spoken `tts_text`.

    - Never lets TTS speak literal 'underscore' or '@' symbols.
    - Maps brand handles & underscored brand names to `brand_spoken_name` ('Bill Explains').
    - Converts complex scientific words, units, and uppercase acronyms to natural spoken forms.
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    pdict = _load_pronunciation_dict()
    spoken_brand = get_brand_spoken_name()

    # 1. Strip markdown & URLs/formatting tokens
    s = re.sub(r"[`*_~#]+", " ", raw)

    # 2. Replace handle variants and configured handle replacements cleanly
    handle_map = dict(pdict.get("handle_replacements") or {})
    for k, v in sorted(handle_map.items(), key=lambda kv: -len(kv[0])):
        s = re.sub(re.escape(k), str(v), s, flags=re.IGNORECASE)
    s = re.sub(r"@?[Bb]ill[_\s]*[Ee]xplains\d*", spoken_brand, s)

    # 3. Convert unit patterns cleanly (e.g. 5000 mAh -> 5000 milliamp hours)
    for item in pdict.get("unit_patterns") or []:
        pat = item.get("pattern")
        rep = item.get("replacement")
        if pat and rep is not None:
            try:
                s = re.sub(pat, rep, s)
            except re.error:
                pass

    # 4. Exact phrases / scientific terms / acronyms
    exact = dict(pdict.get("exact_phrases") or {})
    for term, spoken in sorted(exact.items(), key=lambda kv: -len(kv[0])):
        if not term:
            continue
        # Acronyms (mostly uppercase) match case-sensitively on word boundaries; lowercase science words match case-insensitively
        flags = 0 if any(c.isupper() for c in term) and term != term.capitalize() else re.IGNORECASE
        s = re.sub(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", str(spoken), s, flags=flags)

    # 5. Scrub any remaining underscores or '@' so TTS never reads 'underscore' or 'at'
    s = s.replace("_", " ").replace("@", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_subtitle_text(text: str) -> str:
    """Keep clean standard American English for on-screen subtitles without odd underscores/artifacts."""
    s = (text or "").strip()
    spoken_brand = get_brand_spoken_name()
    s = re.sub(r"[`*]+", "", s)
    s = re.sub(r"@?[Bb]ill_[Ee]xplains\d*", spoken_brand, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def probe_duration(media_path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        text=True,
    ).strip()
    return float(out)


def _group_words_to_chunks(
    word_events: list[tuple[float, float, str]],
    offset_sec: float,
    max_words: int = 4,
    max_chars: int = 24,
) -> list[SubtitleChunk]:
    chunks: list[SubtitleChunk] = []
    buf: list[tuple[float, float, str]] = []
    cur_chars = 0

    def flush() -> None:
        nonlocal buf, cur_chars
        if not buf:
            return
        text = " ".join(w[2] for w in buf).strip()
        start = round(offset_sec + buf[0][0], 3)
        end = round(offset_sec + buf[-1][1], 3)
        if end <= start:
            end = round(start + 0.25, 3)
        chunks.append(SubtitleChunk(start=start, end=end, text=text))
        buf = []
        cur_chars = 0

    for w_start, w_end, word in word_events:
        pause_gap = (w_start - buf[-1][1]) if buf else 0.0
        if buf and (
            pause_gap > 0.16
            or len(buf) >= max_words
            or cur_chars + len(word) + 1 > max_chars
            or buf[-1][2].endswith((".", "!", "?", ","))
        ):
            flush()
        buf.append((w_start, w_end, word))
        cur_chars += len(word) + 1
    flush()
    return chunks


def _fallback_proportional_chunks(
    text: str,
    offset_sec: float,
    speech_duration: float,
    max_words: int = 4,
    max_chars: int = 24,
) -> list[SubtitleChunk]:
    words = [w for w in text.strip().split() if w]
    if not words:
        return []
    weights = [max(len(w), 2) + (3 if w.endswith((".", "!", "?", ",")) else 0) for w in words]
    total_w = sum(weights)
    usable_dur = max(0.3, speech_duration - 0.06)

    cursor = 0.02
    word_events: list[tuple[float, float, str]] = []
    for w, wt in zip(words, weights):
        dur = usable_dur * (wt / total_w)
        word_events.append((cursor, cursor + dur, w))
        cursor += dur
    return _group_words_to_chunks(word_events, offset_sec, max_words=max_words, max_chars=max_chars)


def _chars_alignment_to_words(alignment: dict) -> list[tuple[float, float, str]]:
    """Convert ElevenLabs character-level timestamps into (start, end, word) tuples."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    n = min(len(chars), len(starts), len(ends))
    words: list[tuple[float, float, str]] = []
    cur_chars: list[str] = []
    w_start: float | None = None
    w_end: float = 0.0

    for i in range(n):
        ch = chars[i]
        if ch.isspace():
            if cur_chars and w_start is not None:
                words.append((w_start, max(w_end, w_start + 0.05), "".join(cur_chars)))
                cur_chars = []
                w_start = None
        else:
            if w_start is None:
                w_start = float(starts[i])
            w_end = float(ends[i])
            cur_chars.append(ch)
    if cur_chars and w_start is not None:
        words.append((w_start, max(w_end, w_start + 0.05), "".join(cur_chars)))
    return words


def _check_elevenlabs_quota(api_key: str, needed_chars: int) -> tuple[bool, str]:
    """Pre-check remaining ElevenLabs character credits before burning partial scenes."""
    try:
        resp = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": api_key},
            timeout=15,
        )
        if resp.status_code in (401, 402, 403, 429):
            return False, f"HTTP {resp.status_code}"
        if resp.status_code == 200:
            data = resp.json()
            limit = int(data.get("character_limit") or 0)
            used = int(data.get("character_count") or 0)
            if limit > 0 and (limit - used) < max(50, needed_chars):
                return False, f"remaining credits {limit - used} < needed {needed_chars}"
        return True, "ok"
    except Exception:
        return True, "skip_precheck"


def _synth_one_scene_elevenlabs(
    tts_text: str,
    out_mp3: Path,
    api_key: str,
    voice_id: str,
    model_id: str,
) -> list[tuple[float, float, str]]:
    """Synthesize natural English voiceover via ElevenLabs API with word alignment."""
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": tts_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.48,
            "similarity_boost": 0.78,
            "style": 0.25,
            "use_speaker_boost": True,
        },
    }
    ts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    resp = requests.post(ts_url, headers=headers, json=payload, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        audio_bytes = base64.b64decode(data["audio_base64"])
        out_mp3.write_bytes(audio_bytes)
        alignment = data.get("alignment") or data.get("normalized_alignment") or {}
        return _chars_alignment_to_words(alignment)

    if resp.status_code in (401, 402, 403, 429) or "quota" in resp.text.lower() or "limit" in resp.text.lower():
        raise ElevenLabsQuotaOrAuthError(f"ElevenLabs limit/auth ({resp.status_code}): {resp.text[:180]}")

    raw_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    resp2 = requests.post(
        raw_url,
        headers={**headers, "Accept": "audio/mpeg"},
        json=payload,
        timeout=60,
    )
    if resp2.status_code in (401, 402, 403, 429) or "quota" in resp2.text.lower() or "limit" in resp2.text.lower():
        raise ElevenLabsQuotaOrAuthError(f"ElevenLabs limit/auth ({resp2.status_code}): {resp2.text[:180]}")
    resp2.raise_for_status()
    out_mp3.write_bytes(resp2.content)
    return []


async def _synth_one_scene_edge(
    tts_text: str,
    out_mp3: Path,
    voice: str,
    rate: str,
    pitch: str,
) -> list[tuple[float, float, str]]:
    communicate = edge_tts.Communicate(
        text=tts_text,
        voice=voice,
        rate=rate,
        pitch=pitch,
        boundary="WordBoundary",
    )
    word_events: list[tuple[float, float, str]] = []
    with open(out_mp3, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start_s = chunk["offset"] / 10_000_000.0
                dur_s = chunk["duration"] / 10_000_000.0
                word_events.append((start_s, start_s + dur_s, chunk["text"]))
    return word_events


def _synthesize_all_scenes(
    scenes: list[SceneSpec],
    voice_parts_dir: Path,
    use_elevenlabs: bool,
    el_key: str,
    el_voice: str,
    el_model: str,
    edge_voice: str,
    rate: str,
    pitch: str,
) -> list[tuple[SceneSpec, str, str, Path, list[tuple[float, float, str]]]]:
    results: list[tuple[SceneSpec, str, str, Path, list[tuple[float, float, str]]]] = []
    for scene in scenes:
        sub_text = sanitize_subtitle_text(scene.narration)
        tts_text = normalize_for_tts(sub_text)
        raw_mp3 = voice_parts_dir / f"{scene.scene_id}.mp3"
        if use_elevenlabs:
            word_events = _synth_one_scene_elevenlabs(
                tts_text,
                raw_mp3,
                api_key=el_key,
                voice_id=el_voice,
                model_id=el_model,
            )
        else:
            word_events = asyncio.run(
                _synth_one_scene_edge(tts_text, raw_mp3, voice=edge_voice, rate=rate, pitch=pitch)
            )
        results.append((scene, tts_text, sub_text, raw_mp3, word_events))
    return results


def _align_subtitles_to_duration(
    sub_text: str,
    word_events: list[tuple[float, float, str]],
    offset_sec: float,
    speech_duration: float,
) -> list[SubtitleChunk]:
    """Keep clean standard American English spelling on screen while using real speech timing."""
    if not word_events:
        return _fallback_proportional_chunks(sub_text, offset_sec=offset_sec, speech_duration=speech_duration)
    sub_words = [w for w in sub_text.strip().split() if w]
    max_ev = max(e[1] for e in word_events) if word_events else speech_duration
    scale = (speech_duration / max_ev) if max_ev > 0 else 1.0
    scaled_events = [(s * scale, e * scale, w) for s, e, w in word_events]

    # When normalized TTS tokens differ in count from clean American English words, remap timings linearly over clean words
    if len(sub_words) == len(scaled_events):
        mapped = [(scaled_events[i][0], scaled_events[i][1], sub_words[i]) for i in range(len(sub_words))]
        return _group_words_to_chunks(mapped, offset_sec=offset_sec)
    return _fallback_proportional_chunks(sub_text, offset_sec=offset_sec, speech_duration=speech_duration)


def generate_episode_voiceover(
    scenes: list[SceneSpec],
    out_dir: Path,
    voice: str = DEFAULT_EDGE_VOICE,
    rate: str = "+3%",
    pitch: str = "+2Hz",
    inter_scene_pause_sec: float = 0.14,
) -> tuple[str, list[SceneVoiceResult], list[SubtitleChunk], float, str]:
    """Generate voiceover per scene: ElevenLabs first, clean full-episode fallback to Edge-TTS if limited.

    Returns (voiceover_full_path, scene_results, subtitles, total_duration_sec, engine_label).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    voice_parts_dir = out_dir / "voice_parts"
    voice_parts_dir.mkdir(parents=True, exist_ok=True)
    inter_scene_pause_sec = min(0.15, max(0.06, float(inter_scene_pause_sec)))

    el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    el_voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_ELEVENLABS_VOICE_ID
    el_model = os.environ.get("ELEVENLABS_MODEL_ID", "").strip() or DEFAULT_ELEVENLABS_MODEL_ID
    use_elevenlabs = bool(el_key)

    total_chars = sum(len(normalize_for_tts(s.narration or "")) for s in scenes)
    if use_elevenlabs:
        ok_quota, reason = _check_elevenlabs_quota(el_key, total_chars)
        if not ok_quota:
            print(f"[tts] ElevenLabs quota/auth unavailable ({reason}) — switching cleanly to Edge-TTS ({voice})")
            use_elevenlabs = False
        else:
            print(f"[tts] Using ElevenLabs (voice_id={el_voice}, model={el_model})")
    else:
        print(f"[tts] ELEVENLABS_API_KEY not set — using Edge-TTS ({voice})")

    engine_used = "ElevenLabs" if use_elevenlabs else f"Edge-TTS ({voice})"
    try:
        raw_parts = _synthesize_all_scenes(
            scenes=scenes,
            voice_parts_dir=voice_parts_dir,
            use_elevenlabs=use_elevenlabs,
            el_key=el_key,
            el_voice=el_voice,
            el_model=el_model,
            edge_voice=voice,
            rate=rate,
            pitch=pitch,
        )
    except Exception as exc:
        if use_elevenlabs:
            print(f"[tts] ElevenLabs failed ({exc}) — falling back to Edge-TTS ({voice}) for consistent full episode")
            engine_used = f"Edge-TTS ({voice})"
            raw_parts = _synthesize_all_scenes(
                scenes=scenes,
                voice_parts_dir=voice_parts_dir,
                use_elevenlabs=False,
                el_key="",
                el_voice="",
                el_model="",
                edge_voice=voice,
                rate=rate,
                pitch=pitch,
            )
        else:
            raise

    scene_results: list[SceneVoiceResult] = []
    all_subtitles: list[SubtitleChunk] = []
    concat_inputs: list[Path] = []

    pause_wav = voice_parts_dir / "pause.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{inter_scene_pause_sec:.3f}",
            "-c:a",
            "pcm_s16le",
            str(pause_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cursor = 0.0
    # Trim leading/trailing silence AND compress mid-sentence silences > 0.15s down to 0.15s
    af_trim = (
        "silenceremove=start_periods=1:start_threshold=-45dB:detection=peak:"
        "stop_periods=-1:stop_duration=0.15:stop_threshold=-44dB,"
        "areverse,silenceremove=start_periods=1:start_threshold=-45dB:detection=peak,areverse"
    )
    for idx, (scene, _tts_text, sub_text, raw_mp3, word_events) in enumerate(raw_parts):
        clean_wav = voice_parts_dir / f"{scene.scene_id}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(raw_mp3),
                "-ar",
                "44100",
                "-ac",
                "1",
                "-af",
                af_trim,
                "-c:a",
                "pcm_s16le",
                str(clean_wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        speech_dur = probe_duration(clean_wav)
        subs = _align_subtitles_to_duration(
            sub_text=sub_text,
            word_events=word_events,
            offset_sec=cursor,
            speech_duration=speech_dur,
        )

        is_last = idx == len(raw_parts) - 1
        # Zero dead-air outro: final scene ends cleanly 0.15s after final spoken syllable
        tail_sec = 0.15 if is_last else inter_scene_pause_sec
        seg_dur = speech_dur + tail_sec
        scene_results.append(
            SceneVoiceResult(
                scene_id=scene.scene_id,
                audio_path=str(clean_wav),
                start_sec=round(cursor, 3),
                end_sec=round(cursor + seg_dur, 3),
                duration_sec=round(seg_dur, 3),
                subtitles=subs,
            )
        )
        all_subtitles.extend(subs)
        concat_inputs.append(clean_wav)
        if not is_last:
            concat_inputs.append(pause_wav)
        else:
            tail_wav = voice_parts_dir / "tail_pause.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    f"{tail_sec:.3f}",
                    "-c:a",
                    "pcm_s16le",
                    str(tail_wav),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            concat_inputs.append(tail_wav)
        cursor += seg_dur

    concat_list = voice_parts_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in concat_inputs), encoding="utf-8"
    )
    full_voice = out_dir / "voiceover_full.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-af",
            "loudnorm=I=-15:TP=-1.5:LRA=9",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(full_voice),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    total_dur = probe_duration(full_voice)
    return str(full_voice), scene_results, all_subtitles, total_dur, engine_used
