"""Agent 4 Tool: FFmpeg + Pillow 9:16 Video Renderer for Kimiabill.

Key features:
- Real footage ONLY (center-cropped 9:16 with subtle cinema contrast & top/bottom gradient scrim)
- Crisp kinetic ASS subtitles in lower safe zone
- Minimal animated science formula/callout badges (fade-in/out) ONLY on scenes that need them
- Subtle @kimiabill brand badge
- Supports fast 480p proxy render for Agent 5 QA and 1080p HD render for final output
- Automatically creates a labeled keyframe contact sheet for Agent 5 visual inspection
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.state import RenderPlan, SubtitleChunk, TimelineSegment


FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
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


def _format_ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - math.floor(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_ass_subtitles(
    subtitles: list[SubtitleChunk],
    ass_path: Path,
    width: int,
    height: int,
    font_size_ratio: float = 0.052,
) -> None:
    font_size = max(24, int(round(width * font_size_ratio)))
    margin_v = int(round(height * 0.21))
    margin_lr = int(round(width * 0.07))
    outline = max(3, int(round(font_size * 0.11)))
    shadow = max(2, int(round(font_size * 0.06)))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H0000D4FF,&H00121218,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for idx, sub in enumerate(subtitles):
        start_s = _format_ass_time(sub.start)
        end_s = _format_ass_time(sub.end)
        clean = sub.text.replace("\n", " ").replace("{", "(").replace("}", ")")
        words = clean.split()
        # Highlight the most meaningful word (longest word >= 4 chars) in warm yellow-gold for kinetic pop
        if len(words) >= 2:
            hi_idx = max(range(len(words)), key=lambda i: len(words[i].strip(".,!?")))
            styled_words = []
            for i, w in enumerate(words):
                if i == hi_idx and len(w.strip(".,!?")) >= 4:
                    styled_words.append(f"{{\\c&H0038D9FF&}}{w}{{\\c&H00FFFFFF&}}")
                else:
                    styled_words.append(w)
            text_ass = " ".join(styled_words)
        else:
            text_ass = clean
        # Subtle pop-in animation (92% -> 100% scale over 90ms)
        anim = "{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}"
        lines.append(f"Dialogue: 0,{start_s},{end_s},Default,,0,0,0,,{anim}{text_ass}\n")

    ass_path.write_text("".join(lines), encoding="utf-8")


from src.brand_config import get_brand_name, get_brand_tagline


def _render_brand_overlay_png(out_png: Path, width: int, height: int) -> None:
    """Create a subtle gradient scrim + clean dynamic brand pill header."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Top subtle vignette (0% -> 18% height)
    top_h = int(height * 0.18)
    for y in range(top_h):
        alpha = int(125 * (1.0 - y / top_h) ** 1.7)
        draw.line([(0, y), (width, y)], fill=(8, 12, 20, alpha))

    # Bottom subtle vignette for subtitle readability (64% -> 100% height)
    bot_start = int(height * 0.64)
    bot_h = height - bot_start
    for i in range(bot_h):
        y = bot_start + i
        alpha = int(165 * (i / bot_h) ** 1.5)
        draw.line([(0, y), (width, y)], fill=(8, 12, 20, alpha))

    # Brand pill: dynamic from config/brand.yaml
    font_sz = max(16, int(width * 0.032))
    font = _pick_font(FONT_BOLD_CANDIDATES, font_sz)
    label = f"{get_brand_name().upper()}  •  {get_brand_tagline().upper()}"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x = int(width * 0.035)
    pad_y = int(font_sz * 0.45)
    pill_w = tw + pad_x * 2
    pill_h = th + pad_y * 2
    x0 = (width - pill_w) // 2
    y0 = int(height * 0.065)

    draw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=pill_h // 2,
        fill=(12, 22, 36, 175),
        outline=(56, 217, 255, 210),
        width=max(2, width // 360),
    )
    draw.text(
        (x0 + pad_x, y0 + pad_y - bbox[1]),
        label,
        font=font,
        fill=(242, 249, 255, 255),
    )
    img.save(out_png)


def _render_callout_badge_png(text: str, out_png: Path, width: int, height: int) -> None:
    """Create a sleek translucent science formula/callout pill card in upper safe zone."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_sz = max(18, int(width * 0.036))
    font = _pick_font(FONT_BOLD_CANDIDATES, font_sz)

    # Auto-wrap to max 2 lines if long
    max_text_w = int(width * 0.78)
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_text_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    line_sizes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    max_lw = max((b[2] - b[0]) for b in line_sizes)
    line_h = int(font_sz * 1.32)
    total_th = line_h * len(lines)

    pad_x = int(width * 0.045)
    pad_y = int(font_sz * 0.55)
    card_w = max_lw + pad_x * 2 + int(width * 0.015)
    card_h = total_th + pad_y * 2
    x0 = (width - card_w) // 2
    y0 = int(height * 0.145)
    radius = max(12, int(width * 0.025))

    # Card shadow
    draw.rounded_rectangle(
        [x0 + 3, y0 + 5, x0 + card_w + 3, y0 + card_h + 5],
        radius=radius,
        fill=(0, 0, 0, 110),
    )
    # Card body
    draw.rounded_rectangle(
        [x0, y0, x0 + card_w, y0 + card_h],
        radius=radius,
        fill=(14, 24, 40, 210),
        outline=(255, 206, 68, 235),
        width=max(2, width // 360),
    )
    # Left accent bar
    bar_w = max(5, int(width * 0.012))
    draw.rounded_rectangle(
        [x0 + 4, y0 + 6, x0 + 4 + bar_w, y0 + card_h - 6],
        radius=bar_w // 2,
        fill=(255, 206, 68, 255),
    )

    ty = y0 + pad_y
    for ln, b in zip(lines, line_sizes):
        lw = b[2] - b[0]
        tx = x0 + (card_w - lw) // 2 + bar_w // 2
        draw.text((tx, ty - b[1]), ln, font=font, fill=(255, 252, 240, 255))
        ty += line_h

    img.save(out_png)


def _prepare_segment_video(
    seg: TimelineSegment,
    out_seg_mp4: Path,
    width: int,
    height: int,
    fps: int,
    work_dir: Path,
) -> None:
    """Crop real video footage to 9:16 cleanly, loop if shorter than narration, add optional badge."""
    dur = seg.duration_sec
    # Base filter: loop if needed, trim exact duration, center-crop 9:16, subtle clarity punch
    vf_parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        f"fps={fps}",
        "eq=contrast=1.05:saturation=1.08",
        "format=rgba",
    ]
    base_vf = ",".join(vf_parts)

    inputs = [
        "-stream_loop",
        "-1",
        "-ss",
        f"{seg.footage_start_offset:.2f}",
        "-i",
        seg.footage_path,
    ]

    if seg.overlay_badge:
        badge_png = work_dir / f"badge_{seg.scene_id}.png"
        _render_callout_badge_png(seg.overlay_badge, badge_png, width, height)
        inputs.extend(["-loop", "1", "-t", f"{dur:.3f}", "-i", str(badge_png)])
        fade_out_st = max(0.3, dur - 0.35)
        filter_complex = (
            f"[0:v]{base_vf}[bg];"
            f"[1:v]format=rgba,fade=t=in:st=0.20:d=0.25:alpha=1,"
            f"fade=t=out:st={fade_out_st:.2f}:d=0.25:alpha=1[bdg];"
            f"[bg][bdg]overlay=0:0:shortest=1,format=yuv420p[vout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-t",
            f"{dur:.3f}",
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(out_seg_mp4),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-t",
            f"{dur:.3f}",
            "-vf",
            base_vf + ",format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(out_seg_mp4),
        ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _mix_audio(plan: RenderPlan, bgm_path: Optional[str], out_audio_wav: Path) -> None:
    """Mix cheerful narration + timed SFX cues + subtle science BGM bed."""
    inputs = ["-i", plan.voice_path]
    filter_parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[voice]"]
    mix_labels = ["[voice]"]
    idx = 1

    # Add BGM looped softly underneath with fade out
    if bgm_path and Path(bgm_path).exists():
        inputs.extend(["-stream_loop", "-1", "-i", bgm_path])
        fade_st = max(0.5, plan.total_duration_sec - 1.2)
        filter_parts.append(
            f"[{idx}:a]atrim=0:{plan.total_duration_sec:.3f},"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={plan.bgm_gain_db}dB,"
            f"afade=t=in:st=0:d=0.6,afade=t=out:st={fade_st:.2f}:d=1.1[bgm]"
        )
        mix_labels.append("[bgm]")
        idx += 1

    # Add timed transition SFX + optional animation pop/spark SFX per segment
    for seg in plan.segments:
        if seg.sfx_path and Path(seg.sfx_path).exists():
            delay_ms = max(0, int(round(seg.start_sec * 1000)))
            inputs.extend(["-i", seg.sfx_path])
            lbl = f"sfx{idx}"
            filter_parts.append(
                f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"volume={seg.sfx_gain_db}dB,adelay={delay_ms}|{delay_ms}[{lbl}]"
            )
            mix_labels.append(f"[{lbl}]")
            idx += 1
        if getattr(seg, "animation_sfx_path", None) and Path(str(seg.animation_sfx_path)).exists() and seg.duration_sec >= 1.8:
            anim_delay_ms = max(0, int(round((seg.start_sec + 0.42) * 1000)))
            anim_gain_db = round(float(seg.sfx_gain_db) - 2.5, 1)
            inputs.extend(["-i", str(seg.animation_sfx_path)])
            lbl_anim = f"asfx{idx}"
            filter_parts.append(
                f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"volume={anim_gain_db}dB,adelay={anim_delay_ms}|{anim_delay_ms}[{lbl_anim}]"
            )
            mix_labels.append(f"[{lbl_anim}]")
            idx += 1

    mix_str = "".join(mix_labels)
    filter_parts.append(
        f"{mix_str}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.92[aout]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[aout]",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(out_audio_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def render_video(
    plan: RenderPlan,
    work_dir: Path,
    bgm_path: Optional[str] = None,
) -> tuple[str, str]:
    """Render 9:16 video (proxy 480p or final 1080p) + QA keyframe contact sheet."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if plan.resolution_mode == "proxy_480p":
        width, height, fps, crf = 480, 854, 15, "25"
    else:
        width, height, fps, crf = 1080, 1920, 30, "19"

    seg_dir = work_dir / f"segs_{plan.resolution_mode}_v{plan.version}"
    seg_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare each scene's real footage segment
    seg_files: list[Path] = []
    for seg in plan.segments:
        out_seg = seg_dir / f"{seg.scene_id}.mp4"
        _prepare_segment_video(seg, out_seg, width, height, fps, seg_dir)
        seg_files.append(out_seg)

    # 2. Concatenate segments cleanly
    concat_txt = seg_dir / "concat.txt"
    concat_txt.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in seg_files), encoding="utf-8"
    )
    stitched_mp4 = seg_dir / "stitched_raw.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
            "-c",
            "copy",
            str(stitched_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 3. Generate brand scrim overlay & kinetic ASS subtitles
    brand_png = seg_dir / "brand_overlay.png"
    _render_brand_overlay_png(brand_png, width, height)
    ass_path = seg_dir / "subtitles.ass"
    _write_ass_subtitles(
        plan.subtitles,
        ass_path,
        width=width,
        height=height,
        font_size_ratio=plan.subtitle_font_size_ratio,
    )

    # 4. Mix narration + SFX + BGM
    mixed_wav = seg_dir / "mixed_audio.wav"
    _mix_audio(plan, bgm_path=bgm_path, out_audio_wav=mixed_wav)

    # 5. Final composite (real footage + brand scrim + kinetic ASS subtitles + mixed audio)
    out_path = Path(plan.output_video_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    escaped_ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    filter_complex = (
        f"[0:v][1:v]overlay=0:0,ass='{escaped_ass}',format=yuv420p[vout]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(stitched_mp4),
            "-i",
            str(brand_png),
            "-i",
            str(mixed_wav),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "2:a",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "medium" if plan.resolution_mode == "final_1080p" else "veryfast",
            "-crf",
            crf,
            "-c:a",
            "aac",
            "-b:a",
            "192k" if plan.resolution_mode == "final_1080p" else "128k",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 6. Build visual QA contact sheet (1 mid-scene frame per scene for Agent 5)
    sheet_path = out_path.with_name(f"{out_path.stem}_contact_sheet.jpg")
    _build_contact_sheet(out_path, plan.segments, sheet_path)
    return str(out_path), str(sheet_path)


def _build_contact_sheet(
    video_path: Path,
    segments: list[TimelineSegment],
    out_jpg: Path,
) -> None:
    tmp_dir = out_jpg.parent / "_qa_frames"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    thumbs: list[tuple[str, Image.Image]] = []
    samples: list[tuple[str, float, str]] = [
        (seg.scene_id, seg.start_sec + seg.duration_sec * 0.48, f"{seg.scene_id} ({seg.start_sec:.1f}-{seg.end_sec:.1f}s)")
        for seg in segments
    ]
    if segments:
        end_t = max(0.0, segments[-1].end_sec - 0.6)
        samples.append(("zz_final", end_t, f"FINAL CTA ({end_t:.1f}s)"))
    for key, mid, label in samples:
        frame_p = tmp_dir / f"{key}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{mid:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(frame_p),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if frame_p.exists():
            im = Image.open(frame_p).convert("RGB")
            im.thumbnail((320, 568))
            thumbs.append((label, im))

    if not thumbs:
        return
    cols = min(3, len(thumbs))
    rows = math.ceil(len(thumbs) / cols)
    tw, th = thumbs[0][1].size
    pad = 16
    hdr = 30
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + hdr + pad) + pad), (18, 20, 26))
    draw = ImageDraw.Draw(sheet)
    font = _pick_font(FONT_BOLD_CANDIDATES, 16)
    for i, (label, im) in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + hdr + pad)
        draw.text((x + 4, y + 4), label, fill=(235, 240, 250), font=font)
        sheet.paste(im, (x, y + hdr))
    sheet.save(out_jpg, quality=88)
