"""Layer 0-Token Deterministic QA Gate.

Runs fast FFmpeg/ffprobe technical checks BEFORE invoking Agent 5 (multimodal LLM),
catching resolution, duration, audio loudness/clipping, and black-frame bugs for 0 tokens.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from src.state import Gate0CheckResult


def run_gate0_checks(
    video_path: str | Path,
    expected_width: int,
    expected_height: int,
    min_duration_sec: float = 20.0,
    max_duration_sec: float = 85.0,
) -> Gate0CheckResult:
    p = Path(video_path)
    issues: list[str] = []

    # 1. Resolution & duration via ffprobe
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

    # 2. Audio loudness & clipping check (volumedetect)
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

    # 3. Black frame detection
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
