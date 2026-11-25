"""Agent 7 Tool: High-CTR 9:16 Cover Thumbnail Generator for Kimiabill Shorts/Reels/TikTok."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from src.tools.mechanistic_animator import (
    BLUE,
    CYAN,
    INK,
    ORANGE,
    RED,
    WHITE,
    YELLOW,
    MascotLibrary,
    _load_fonts,
    draw_brand_pill,
    draw_comic_box,
    draw_outlined_text,
)
from src.tools.video_renderer import FONT_BOLD_CANDIDATES, _pick_font


def extract_bg_frame(video_path: Path, timestamp_sec: float, out_jpg: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, timestamp_sec):.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-q:v",
            "2",
            str(out_jpg),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_jpg


def _fit_headline_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    start_size: int,
    min_size: int,
    max_w: int = 880,
    max_lines: int = 2,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Auto-shrink & wrap headline text so characters NEVER clip off canvas edges."""
    clean = " ".join((text or "").strip().split()).upper()
    if not clean:
        return _pick_font(FONT_BOLD_CANDIDATES, start_size), [""]

    for sz in range(start_size, min_size - 1, -4):
        fnt = _pick_font(FONT_BOLD_CANDIDATES, sz)
        if draw.textbbox((0, 0), clean, font=fnt)[2] <= max_w:
            return fnt, [clean]

    words = clean.split()
    for sz in range(start_size - 6, min_size - 1, -4):
        fnt = _pick_font(FONT_BOLD_CANDIDATES, sz)
        lines: list[str] = []
        cur = ""
        overflow = False
        for w in words:
            cand = f"{cur} {w}".strip()
            if draw.textbbox((0, 0), cand, font=fnt)[2] <= max_w:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                if draw.textbbox((0, 0), w, font=fnt)[2] > max_w:
                    overflow = True
                    break
                cur = w
        if cur:
            lines.append(cur)
        if not overflow and len(lines) <= max_lines:
            return fnt, lines

    fnt = _pick_font(FONT_BOLD_CANDIDATES, min_size)
    mid = max(1, len(words) // 2)
    return fnt, [" ".join(words[:mid]), " ".join(words[mid:])]


def _paste_rounded_spotlight(
    canvas: Image.Image,
    raw_frame: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 34,
) -> None:
    """Create a crisp un-blurred center spotlight card from the real science visual."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    # Take the sharp middle band of the 1080x1920 source (away from top/bottom corner watermarks)
    crop_h = int(1080 * (h / w))
    cy = max(crop_h // 2 + 120, min(1920 - crop_h // 2 - 140, 900))
    sharp = raw_frame.crop((0, cy - crop_h // 2, 1080, cy + crop_h // 2)).resize(
        (w, h), Image.Resampling.LANCZOS
    )
    sharp = ImageEnhance.Contrast(sharp).enhance(1.24)
    sharp = ImageEnhance.Color(sharp).enhance(1.22)
    sharp = ImageEnhance.Sharpness(sharp).enhance(1.18).convert("RGBA")

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    canvas.paste(sharp, (x0, y0), mask)


def generate_cover_thumbnail(
    bg_video_path: Path,
    bg_timestamp_sec: float,
    headline_top: str,
    headline_main: str,
    badge_text: str,
    out_jpg: Path,
    mascots_dir: Optional[Path] = None,
    use_mascot: bool = True,
) -> str:
    """Create a high-CTR, crop-safe 1080x1920 science poster thumbnail with crisp spotlight visual."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    tmp_frame = out_jpg.with_name(f"_tmp_{out_jpg.stem}.jpg")
    extract_bg_frame(bg_video_path, bg_timestamp_sec, tmp_frame)

    raw_src = Image.open(tmp_frame).convert("RGB")
    tmp_frame.unlink(missing_ok=True)

    # Rich blurred + deep-navy-graded backdrop so hero typography & spotlight card pop cleanly
    bg_blur = raw_src.filter(ImageFilter.GaussianBlur(radius=16))
    bg_blur = ImageEnhance.Brightness(bg_blur).enhance(0.52)
    bg_blur = ImageEnhance.Color(bg_blur).enhance(1.15)
    base = bg_blur.convert("RGBA")

    scrim = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    sd.rectangle([0, 0, 1080, 1920], fill=(10, 16, 38, 125))
    # Subtle radial spotlight glow behind middle card
    sd.ellipse([90, 620, 990, 1240], fill=(32, 110, 210, 65))
    base.alpha_composite(scrim)

    fonts = _load_fonts()
    draw = ImageDraw.Draw(base)

    # Top-left dynamic @handle pill + top-right SAFE ZONE badge
    draw_brand_pill(draw, fonts)

    badge_clean = (badge_text or "EVERYDAY TECH").strip().upper()[:22]
    b_font = _pick_font(FONT_BOLD_CANDIDATES, 32)
    bb = draw.textbbox((0, 0), badge_clean, font=b_font)
    bw = min(460, (bb[2] - bb[0]) + 64)
    draw_comic_box(
        draw,
        (540 - bw // 2, 232, 540 + bw // 2, 304),
        fill=ORANGE,
        radius=32,
        border_w=5,
        shadow_offset=7,
    )
    draw_outlined_text(draw, (540, 268), badge_clean, b_font, fill=WHITE, stroke_w=4)

    # Center-safe Hero Headline Backdrop + Auto-fitted Giant Two-Part Typography (max width 880px)
    f_top, lines_top = _fit_headline_lines(draw, headline_top, start_size=74, min_size=48, max_w=880, max_lines=2)
    f_main, lines_main = _fit_headline_lines(draw, headline_main, start_size=90, min_size=54, max_w=880, max_lines=2)

    top_step = int(f_top.size * 1.15)
    main_step = int(f_main.size * 1.14)
    block_h = len(lines_top) * top_step + len(lines_main) * main_step + 44
    hy0 = 332
    hy1 = min(690, hy0 + block_h)
    draw_comic_box(
        draw,
        (72, hy0, 1008, hy1),
        fill=(14, 20, 44, 218),
        outline=CYAN,
        radius=30,
        border_w=5,
        shadow_offset=10,
    )

    cur_y = hy0 + 26 + top_step // 2
    for ln in lines_top:
        draw_outlined_text(draw, (540, cur_y), ln, f_top, fill=WHITE, stroke_w=8)
        cur_y += top_step
    cur_y += (main_step - top_step) // 2 + 4
    for ln in lines_main:
        draw_outlined_text(draw, (540, cur_y), ln, f_main, fill=YELLOW, stroke_w=10)
        cur_y += main_step

    # Crisp Un-Blurred Real Science Visual Card inside 4:5 / 3:4 Safe Zone
    card_y0 = hy1 + 36
    card_y1 = card_y0 + 520
    _paste_rounded_spotlight(base, raw_src, (104, card_y0, 976, card_y1), radius=36)
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle([104, card_y0, 976, card_y1], radius=36, outline=YELLOW, width=7)

    # Punchy Comic '!' and '?' stickers flanking the spotlight card
    draw_outlined_text(draw, (112, card_y0 + 26), "?", fonts["giant_sym"], fill=RED, stroke_w=11)
    draw_outlined_text(draw, (966, card_y0 + 24), "!", fonts["giant_sym"], fill=CYAN, stroke_w=11)

    # Bottom safe-zone Duo Opsi B Mascots pointing cleanly upward at the visual
    if use_mascot and mascots_dir:
        lib = MascotLibrary(mascots_dir)
        m_bot = min(1570, card_y1 + 450)
        lib.paste(base, "male_curious", center_x=270, bottom_y=m_bot, target_h=455, local_t=1.0, appear_at=0.0)
        lib.paste(base, "female_curious", center_x=810, bottom_y=m_bot, target_h=445, local_t=1.0, appear_at=0.0)

    out_rgb = base.convert("RGB")
    out_rgb.save(out_jpg, quality=90, optimize=True)
    return str(out_jpg)
