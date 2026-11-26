"""Hybrid 30fps Mechanistic Comic-Science Animation Engine for Kimiabill_videos.

Renders crisp flat-vector comic-science animations (rounded cards with bold dark outlines,
step-by-step molecular/physical simulations, animated counters/bars, diagonal wipe transitions,
TERJAWAB! outro, and optional animated Opsi B Duo mascots) directly as 30fps RGBA frames
piped into FFmpeg and composited cleanly over REAL video footage.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.state import RenderPlan, SubtitleChunk, TimelineSegment
from src.tools.video_renderer import (
    FONT_BOLD_CANDIDATES,
    FONT_REG_CANDIDATES,
    _build_contact_sheet,
    _mix_audio,
    _pick_font,
    _write_ass_subtitles,
)


# Comic-science palette inspired by reference style
INK = (18, 22, 34, 255)
SHADOW = (10, 14, 24, 140)
CARD_BG = (252, 253, 255, 242)
CARD_DARK = (22, 26, 48, 244)
WHITE = (255, 255, 255, 255)
YELLOW = (255, 214, 51, 255)
GOLD = (255, 196, 28, 255)
CYAN = (48, 214, 255, 255)
BLUE = (56, 132, 255, 255)
PURPLE = (128, 72, 232, 255)
ORANGE = (255, 132, 42, 255)
RED = (238, 66, 92, 255)
GREEN = (36, 196, 124, 255)
RUST_LIGHT = (224, 114, 42, 255)
RUST_DARK = (158, 64, 24, 255)
IRON_GRAY = (146, 158, 174, 255)
IRON_DARK = (92, 104, 120, 255)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def ease_out_back(t: float) -> float:
    t = clamp(t)
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((t - 1.0) ** 3) + c1 * ((t - 1.0) ** 2)


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int] = WHITE,
    outline_color: tuple[int, int, int, int] = INK,
    stroke_w: int = 5,
    anchor: str = "mm",
) -> None:
    draw.text(
        pos,
        text,
        font=font,
        fill=fill,
        anchor=anchor,
        stroke_width=stroke_w,
        stroke_fill=outline_color,
    )


def draw_comic_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int, int] = CARD_BG,
    outline: tuple[int, int, int, int] = INK,
    radius: int = 28,
    border_w: int = 6,
    shadow_offset: int = 10,
) -> None:
    x0, y0, x1, y1 = box
    if shadow_offset > 0:
        draw.rounded_rectangle(
            [x0 + shadow_offset, y0 + shadow_offset, x1 + shadow_offset, y1 + shadow_offset],
            radius=radius,
            fill=SHADOW,
        )
    draw.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=radius,
        fill=fill,
        outline=outline,
        width=border_w,
    )


from src.brand_config import get_brand_handle, get_brand_name, get_watermark_path


def draw_brand_pill(draw: ImageDraw.ImageDraw, fonts: dict) -> None:
    """Top-left dynamic @handle brand badge with glowing microchip icon."""
    handle = get_brand_handle()
    x0, y0, x1, y1 = 48, 68, max(355, 130 + len(handle) * 20), 144
    draw.rounded_rectangle([x0 + 4, y0 + 6, x1 + 4, y1 + 6], radius=38, fill=(0, 0, 0, 120))
    draw.rounded_rectangle([x0, y0, x1, y1], radius=38, fill=(20, 26, 48, 225), outline=WHITE, width=4)
    # Mini microchip icon (covered by Duo Mascot badge when watermark.png is present)
    fx, fy = x0 + 44, (y0 + y1) // 2
    for dxy in (-10, 0, 10):
        draw.line([(fx + dxy, fy - 20), (fx + dxy, fy + 20)], fill=CYAN, width=2)
        draw.line([(fx - 20, fy + dxy), (fx + 20, fy + dxy)], fill=CYAN, width=2)
    draw.rounded_rectangle([fx - 13, fy - 13, fx + 13, fy + 13], radius=4, fill=(20, 26, 48, 255), outline=CYAN, width=3)
    draw.text((x0 + 84, (y0 + y1) // 2), handle, font=fonts["brand"], fill=WHITE, anchor="lm")


def _load_watermark_badges() -> tuple[Optional[Image.Image], Optional[Image.Image]]:
    """Load and pre-scale the Duo Mascot watermark PNG (pill badge + translucent corner stamp)."""
    wm_path = get_watermark_path()
    if not wm_path.exists():
        return None, None
    src = Image.open(wm_path).convert("RGBA")
    pill_icon = src.resize((68, 68), Image.Resampling.LANCZOS)
    corner = src.resize((96, 96), Image.Resampling.LANCZOS)
    r, g, b, a = corner.split()
    a = a.point(lambda v: int(v * 0.62))
    corner = Image.merge("RGBA", (r, g, b, a))
    return pill_icon, corner


def draw_mascot_watermark(
    canvas: Image.Image,
    pill_icon: Optional[Image.Image],
    corner_stamp: Optional[Image.Image],
) -> None:
    """Paste channel mascot badge onto top-left @handle pill + subtle translucent corner watermark."""
    if pill_icon is not None:
        canvas.alpha_composite(pill_icon, (54, 72))
    if corner_stamp is not None:
        canvas.alpha_composite(corner_stamp, (952, 815))


def draw_header_pill(
    draw: ImageDraw.ImageDraw,
    text: str,
    bg_color: tuple[int, int, int, int],
    fonts: dict,
    local_t: float,
    y_center: int = 225,
) -> None:
    scale = ease_out_back(local_t / 0.35)
    if scale <= 0.05:
        return
    bbox = draw.textbbox((0, 0), text, font=fonts["header"])
    tw = bbox[2] - bbox[0]
    pw = int((tw + 84) * min(scale, 1.05))
    ph = int(76 * min(scale, 1.05))
    x0 = 540 - pw // 2
    y0 = y_center - ph // 2
    draw_comic_box(draw, (x0, y0, x0 + pw, y0 + ph), fill=bg_color, radius=24, border_w=5, shadow_offset=8)
    draw_outlined_text(draw, (540, y_center), text, fonts["header"], fill=WHITE, stroke_w=4)


def draw_takeaway_card(
    draw: ImageDraw.ImageDraw,
    top_label: str,
    big_word: str,
    big_color: tuple[int, int, int, int],
    sub_label: str,
    fonts: dict,
    local_t: float,
    appear_at: float,
    center_x: int = 540,
    y0: int = 865,
    card_w: int = 620,
) -> None:
    if local_t < appear_at:
        return
    p = ease_out_back((local_t - appear_at) / 0.35)
    sub_lines = _wrap_text(draw, sub_label, fonts["tiny_bold"], max(260, card_w - 72), max_lines=2)
    base_h = 266 if len(sub_lines) >= 2 else 235
    w = int(card_w * min(p, 1.03))
    h = int(base_h * min(p, 1.03))
    x0 = center_x - w // 2
    yc = y0 + base_h // 2
    top_y = yc - h // 2
    draw_comic_box(draw, (x0, top_y, x0 + w, top_y + h), fill=CARD_BG, radius=26, border_w=5, shadow_offset=8)
    if p > 0.65:
        draw.text((center_x, top_y + 42), top_label, font=fonts["small_bold"], fill=INK, anchor="mm")
        draw_outlined_text(draw, (center_x, top_y + 114), big_word, fonts["big_kw"], fill=big_color, stroke_w=5)
        sy = top_y + 184
        for ln in sub_lines:
            draw.text((center_x, sy), ln, font=fonts["tiny_bold"], fill=(60, 68, 86, 255), anchor="mm")
            sy += 34


def draw_diagonal_wipe(draw: ImageDraw.ImageDraw, progress: float, w: int = 1080, h: int = 1920) -> None:
    """Multi-color diagonal comic wipe transition matching the reference videos."""
    if progress <= 0.0 or progress >= 1.0:
        return
    # Sweep from right (+w+600) to left (-700)
    sweep_x = int((w + 700) - progress * (w + 1500))
    skew = 320
    bands = [
        (0, 120, BLUE),
        (120, 210, YELLOW),
        (210, 310, RED),
        (310, 520, BLUE),
    ]
    for dx0, dx1, col in bands:
        pts = [
            (sweep_x + dx0 + skew, 0),
            (sweep_x + dx1 + skew, 0),
            (sweep_x + dx1 - skew, h),
            (sweep_x + dx0 - skew, h),
        ]
        draw.polygon(pts, fill=col)


def draw_sunburst_bg(w: int, h: int, angle_offset: float = 0.0) -> Image.Image:
    """Warm golden comic sunburst background for the TERJAWAB! outro."""
    img = Image.new("RGBA", (w, h), (255, 194, 34, 255))
    draw = ImageDraw.Draw(img)
    cx, cy = w // 2, int(h * 0.45)
    r = int(math.hypot(w, h))
    num_rays = 20
    for i in range(num_rays):
        a0 = (i * 2 * math.pi / num_rays) + angle_offset
        a1 = a0 + (math.pi / num_rays)
        pts = [
            (cx, cy),
            (cx + int(r * math.cos(a0)), cy + int(r * math.sin(a0))),
            (cx + int(r * math.cos(a1)), cy + int(r * math.sin(a1))),
        ]
        draw.polygon(pts, fill=(255, 224, 92, 235))
    return img


class MascotLibrary:
    def __init__(self, mascots_dir: Path) -> None:
        self.sprites: dict[str, Image.Image] = {}
        for name in (
            "male_curious",
            "female_curious",
            "male_explain",
            "female_explain",
            "male_celebrate",
            "female_celebrate",
        ):
            p = mascots_dir / f"{name}.png"
            if p.exists():
                self.sprites[name] = Image.open(p).convert("RGBA")

    def paste(
        self,
        canvas: Image.Image,
        key: str,
        center_x: int,
        bottom_y: int,
        target_h: int,
        local_t: float,
        appear_at: float = 0.0,
        flip_x: bool = False,
    ) -> None:
        if key not in self.sprites or local_t < appear_at:
            return
        sp = self.sprites[key]
        p = ease_out_back((local_t - appear_at) / 0.40)
        bob = int(math.sin((local_t - appear_at) * 4.2) * 8)
        h = max(20, int(target_h * min(p, 1.04)))
        w = max(20, int(sp.width * (h / sp.height)))
        resized = sp.resize((w, h), Image.Resampling.LANCZOS)
        if flip_x:
            resized = resized.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        x = center_x - w // 2
        y = bottom_y - h + bob
        canvas.alpha_composite(resized, (x, y))


# === SCENE-SPECIFIC MECHANISTIC ANIMATIONS (Korosi Episode 1) ===

def draw_scene1_hook(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    # Subtle top/bottom contrast scrim so real glowing iron footage shines cleanly in center
    for y in range(0, 520, 4):
        a = int(145 * (1.0 - y / 520) ** 1.4)
        draw.rectangle([0, y, 1080, y + 4], fill=(14, 16, 36, a))

    # Punchy comic hook title popping in
    if t >= 0.05:
        p1 = ease_out_back(t / 0.32)
        y1 = int(250 - (1.0 - min(p1, 1.0)) * 40)
        draw_outlined_text(draw, (540, y1), "WHY DOES IRON", fonts["hook_sub"], fill=WHITE, stroke_w=9)
    if t >= 0.25:
        p2 = ease_out_back((t - 0.25) / 0.35)
        y2 = int(355 - (1.0 - min(p2, 1.0)) * 35)
        draw_outlined_text(draw, (540, y2), "ACTUALLY RUST?!", fonts["hook_main"], fill=YELLOW, stroke_w=11)
    if t >= 0.65:
        p3 = ease_out_back((t - 0.65) / 0.35)
        y3 = int(450 + (1.0 - min(p3, 1.0)) * 20)
        draw_comic_box(draw, (185, y3 - 34, 895, y3 + 34), fill=ORANGE, radius=32, border_w=5, shadow_offset=6)
        draw_outlined_text(draw, (540, y3), "IT'S SLOWLY BURNING IN AIR!", fonts["small_bold"], fill=WHITE, stroke_w=4)

    # Bobbing comic '?' and '!' icons
    bob1 = int(math.sin(t * 5.0) * 14)
    bob2 = int(math.cos(t * 4.8) * 14)
    draw_outlined_text(draw, (135, 640 + bob1), "?", fonts["giant_sym"], fill=RED, stroke_w=10)
    draw_outlined_text(draw, (945, 630 + bob2), "!", fonts["giant_sym"], fill=CYAN, stroke_w=10)

    if mascots:
        mascots.paste(canvas, "male_curious", center_x=235, bottom_y=1315, target_h=430, local_t=t, appear_at=0.15)
        mascots.paste(canvas, "female_curious", center_x=845, bottom_y=1315, target_h=420, local_t=t, appear_at=0.28)


def draw_scene2_oxidation_compare(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "OXIDATION = SLOW BURNING", ORANGE, fonts, t)

    # Mechanistic card: Fast Fire Oxidation vs Slow Rust Oxidation
    bx0, by0, bx1, by1 = 68, 300, 1012, 815
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)
    # Center divider
    draw.line([(540, by0 + 26), (540, by1 - 26)], fill=(210, 218, 232, 255), width=4)

    # Left: FIRE (Fast Oxidation)
    draw.rounded_rectangle([110, 330, 500, 388], radius=18, fill=RED, outline=INK, width=4)
    draw_outlined_text(draw, (305, 359), "FIRE (FAST)", fonts["small_bold"], fill=WHITE, stroke_w=3)
    # Animated flame pulsing
    pulse = 1.0 + 0.12 * math.sin(t * 14.0)
    fx, fy = 305, 575
    fh = int(145 * pulse)
    fw = int(78 * pulse)
    draw.polygon(
        [(fx, fy - fh), (fx + fw, fy + 10), (fx, fy + 55), (fx - fw, fy + 10)],
        fill=ORANGE,
        outline=INK,
    )
    draw.polygon(
        [(fx, fy - int(fh * 0.62)), (fx + int(fw * 0.55), fy + 8), (fx, fy + 36), (fx - int(fw * 0.55), fy + 8)],
        fill=YELLOW,
    )
    # Sparks orbiting flame
    for i in range(5):
        ang = t * 5.0 + i * 1.25
        sx = fx + int(math.cos(ang) * (75 + (i % 2) * 28))
        sy = fy - 45 - int(((t * 90 + i * 35) % 120))
        draw.ellipse([sx - 6, sy - 6, sx + 6, sy + 6], fill=YELLOW, outline=INK, width=2)
    draw.text((305, 675), "Fe + O₂ + Heat", font=fonts["tiny_bold"], fill=INK, anchor="mm")
    draw.rounded_rectangle([140, 712, 470, 766], radius=16, fill=(255, 236, 236, 255), outline=RED, width=3)
    draw.text((305, 739), "Takes SECONDS", font=fonts["tiny_bold"], fill=RED, anchor="mm")

    # Right: RUST (Slow Oxidation)
    draw.rounded_rectangle([580, 330, 970, 388], radius=18, fill=RUST_LIGHT, outline=INK, width=4)
    draw_outlined_text(draw, (775, 359), "RUST (SLOW)", fonts["small_bold"], fill=WHITE, stroke_w=3)
    # Iron bar slowly getting covered by creeping rust spots
    draw.rounded_rectangle([620, 515, 930, 595], radius=16, fill=IRON_GRAY, outline=INK, width=5)
    progress = clamp(t / max(dur * 0.85, 1.0))
    rust_w = int(300 * progress)
    if rust_w > 12:
        draw.rounded_rectangle([624, 519, 624 + rust_w, 591], radius=12, fill=RUST_LIGHT)
        # Rust speckles
        for k in range(14):
            rx = 638 + int((k * 23) % max(rust_w - 10, 1))
            ry = 532 + (k * 17) % 44
            if rx < 620 + rust_w:
                draw.ellipse([rx - 7, ry - 7, rx + 7, ry + 7], fill=RUST_DARK)
    # Gentle floating O2 bubbles approaching iron bar
    for j in range(3):
        oy = 430 + int(((t * 32 + j * 28) % 75))
        ox = 675 + j * 95
        draw.ellipse([ox - 20, oy - 20, ox + 20, oy + 20], fill=CYAN, outline=INK, width=3)
        draw.text((ox, oy), "O₂", font=fonts["micro_bold"], fill=INK, anchor="mm")

    draw.text((775, 675), "Fe + O₂ + H₂O (No Flame)", font=fonts["tiny_bold"], fill=INK, anchor="mm")
    draw.rounded_rectangle([610, 712, 940, 766], radius=16, fill=(255, 243, 228, 255), outline=RUST_LIGHT, width=3)
    draw.text((775, 739), "Takes DAYS / MONTHS", font=fonts["tiny_bold"], fill=RUST_DARK, anchor="mm")

    # Key takeaway card (shifts slightly left if mascot is on right)
    tk_x = 380 if mascots else 540
    tk_w = 570 if mascots else 660
    draw_takeaway_card(
        draw,
        "Both are forms of",
        "OXIDATION!",
        ORANGE,
        "Iron reacts directly with oxygen",
        fonts,
        t,
        appear_at=2.6,
        center_x=tk_x,
        y0=865,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "male_explain", center_x=865, bottom_y=1290, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene3_atomic_mechanism(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "1: RUST CHEMISTRY REACTION", BLUE, fonts, t)

    # Atomic Simulation Box
    bx0, by0, bx1, by1 = 68, 300, 1012, 835
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)

    # Bottom iron lattice (2 rows of Fe atoms)
    progress = clamp(t / max(dur * 0.85, 1.0))
    rust_count = int(progress * 8)

    draw.rectangle([94, 650, 986, 765], fill=(224, 230, 238, 255), outline=INK, width=4)
    # Row 2 (bottom solid iron)
    for i in range(8):
        ax = 152 + i * 110
        ay = 725
        draw.ellipse([ax - 28, ay - 28, ax + 28, ay + 28], fill=IRON_GRAY, outline=INK, width=4)
        draw.text((ax, ay), "Fe", font=fonts["micro_bold"], fill=WHITE, anchor="mm")

    # Row 1 (surface Fe atoms gradually oxidizing into orange rust Fe2O3!)
    for i in range(8):
        ax = 152 + i * 110
        ay = 648
        is_rusted = i < rust_count
        col = RUST_LIGHT if is_rusted else IRON_GRAY
        label = "Fe³⁺" if is_rusted else "Fe"
        draw.ellipse([ax - 30, ay - 30, ax + 30, ay + 30], fill=col, outline=INK, width=4)
        draw.text((ax, ay), label, font=fonts["micro_bold"], fill=WHITE, anchor="mm")

        # Animated yellow electrons (e-) flying upward as each atom oxidizes!
        atom_t = progress * 8 - i
        if 0.0 < atom_t < 2.2:
            ey = int(ay - 40 - atom_t * 78)
            ex = ax + int(math.sin(atom_t * 6) * 18)
            if ey > by0 + 30:
                draw.ellipse([ex - 20, ey - 20, ex + 20, ey + 20], fill=YELLOW, outline=INK, width=3)
                draw.text((ex, ey), "e⁻", font=fonts["micro_bold"], fill=INK, anchor="mm")

    # Incoming H2O (cyan) and O2 (red) molecules landing onto the iron surface
    for m in range(4):
        cyc = (t * 0.75 + m * 0.27) % 1.0
        mx = 195 + m * 225
        my = int(345 + ease_out_cubic(cyc) * 235)
        if m % 2 == 0:
            draw.ellipse([mx - 34, my - 28, mx + 34, my + 28], fill=CYAN, outline=INK, width=4)
            draw.text((mx, my), "H₂O", font=fonts["micro_bold"], fill=INK, anchor="mm")
        else:
            draw.ellipse([mx - 32, my - 28, mx + 32, my + 28], fill=RED, outline=INK, width=4)
            draw.text((mx, my), "O₂", font=fonts["micro_bold"], fill=WHITE, anchor="mm")

    # Live counter bar at bottom of diagram box (just like reference virus_dubbed.mp4!)
    draw.text(
        (540, 800),
        f"Atoms turned into iron oxide (rust): {rust_count} / 8",
        font=fonts["tiny_bold"],
        fill=RUST_DARK,
        anchor="mm",
    )

    # Equation & takeaway card
    tk_x = 700 if mascots else 540
    tk_w = 570 if mascots else 720
    draw_takeaway_card(
        draw,
        "Rust Formation Formula",
        "Fe₂O₃·nH₂O",
        RUST_LIGHT,
        "Fe + O₂ + H₂O → Orange Iron Oxide",
        fonts,
        t,
        appear_at=1.6,
        center_x=tk_x,
        y0=875,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "female_explain", center_x=215, bottom_y=1295, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene4_porous_rust(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "2: RUST IS POROUS!", PURPLE, fonts, t)

    bx0, by0, bx1, by1 = 68, 300, 1012, 825
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)

    # Animated cross-section: porous rust eating deeper into iron core!
    progress = clamp(t / max(dur * 0.85, 1.0))
    rust_depth = int(110 + progress * 115)  # Rust expands downward over time!

    # Fresh iron core at bottom
    draw.rectangle([120, 520, 960, 765], fill=IRON_GRAY, outline=INK, width=5)
    draw.text((540, 720), "HEALTHY IRON CORE (STILL CORRODING)", font=fonts["tiny_bold"], fill=WHITE, anchor="mm")

    # Expanding porous rust crust
    draw.rectangle([120, 445, 960, 445 + rust_depth], fill=RUST_LIGHT, outline=INK, width=5)
    # Visible dark pores / cracks inside the rust crust!
    pores = [(240, 465), (420, 480), (630, 460), (810, 475), (330, 525), (540, 515), (735, 530)]
    for px, py in pores:
        if py < 435 + rust_depth:
            draw.polygon(
                [(px - 18, py), (px + 18, py), (px + 8, py + 48), (px - 10, py + 48)],
                fill=(76, 36, 18, 255),
            )
    draw_outlined_text(draw, (540, 485), "Brittle & Cracked Rust Layer", fonts["small_bold"], fill=WHITE, stroke_w=4)

    # Animated H2O & O2 arrows penetrating straight through the cracks!
    for idx, ax in enumerate((240, 420, 630, 810)):
        cyc = (t * 0.85 + idx * 0.22) % 1.0
        ay = int(335 + cyc * (rust_depth + 115))
        col = CYAN if idx % 2 == 0 else RED
        lbl = "H₂O" if idx % 2 == 0 else "O₂"
        draw.line([(ax, ay - 26), (ax, ay + 10)], fill=col, width=7)
        draw.polygon([(ax, ay + 24), (ax - 14, ay + 4), (ax + 14, ay + 4)], fill=col, outline=INK)
        draw.ellipse([ax - 26, ay - 52, ax + 26, ay - 14], fill=col, outline=INK, width=3)
        draw.text((ax, ay - 33), lbl, font=fonts["micro_bold"], fill=INK if col == CYAN else WHITE, anchor="mm")

    tk_x = 370 if mascots else 540
    tk_w = 595 if mascots else 680
    draw_takeaway_card(
        draw,
        "Iron rust is naturally",
        "POROUS!",
        PURPLE,
        "Water & O₂ keep seeping inside",
        fonts,
        t,
        appear_at=1.6,
        center_x=tk_x,
        y0=870,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "male_curious", center_x=875, bottom_y=1295, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene5_saltwater_race(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "3: BEACH & SALTWATER EFFECT", RED, fonts, t)

    bx0, by0, bx1, by1 = 68, 300, 1012, 825
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)
    draw.text((540, 350), "Rust Formation Speed on Iron", font=fonts["small_bold"], fill=INK, anchor="mm")

    progress = ease_out_cubic(clamp(t / max(dur * 0.72, 1.0)))

    # Bar 1: Fresh Water (slow)
    draw.text((130, 445), "Fresh Water", font=fonts["small_bold"], fill=BLUE, anchor="lm")
    draw.rounded_rectangle([130, 478, 945, 545], radius=22, fill=(230, 236, 245, 255), outline=INK, width=4)
    w1 = max(24, int(805 * 0.28 * progress))
    draw.rounded_rectangle([135, 483, 135 + w1, 540], radius=18, fill=BLUE)
    draw.text((155 + w1, 512), f"{int(28 * progress)}%", font=fonts["small_bold"], fill=INK, anchor="lm")

    # Bar 2: Saltwater / Coastal Air (FAST + electric sparks!)
    draw.text((130, 610), "Coastal Saltwater (Na⁺ Cl⁻)", font=fonts["small_bold"], fill=RED, anchor="lm")
    draw.rounded_rectangle([130, 643, 945, 718], radius=22, fill=(252, 232, 232, 255), outline=INK, width=4)
    w2 = max(24, int(805 * 0.94 * progress))
    draw.rounded_rectangle([135, 648, 135 + w2, 713], radius=18, fill=RED)
    draw_outlined_text(draw, (min(885, 135 + w2 - 55), 680), f"{int(94 * progress)}%", fonts["small_bold"], fill=YELLOW, stroke_w=4)

    # Animated electric bolt sparks along the salt-water bar
    for s in range(4):
        sx = 180 + int(((t * 340 + s * 170) % max(w2 - 40, 40)))
        sy = 680 + int(math.sin(t * 20 + s) * 14)
        draw_outlined_text(draw, (sx, sy), "⚡", fonts["small_bold"], fill=YELLOW, stroke_w=2)

    draw.text((540, 772), "Salt ions (Na⁺, Cl⁻) = micro electrical bridge!", font=fonts["tiny_bold"], fill=(60, 68, 86, 255), anchor="mm")

    tk_x = 710 if mascots else 540
    tk_w = 595 if mascots else 680
    draw_takeaway_card(
        draw,
        "Near the ocean, rust is",
        "MUCH FASTER!",
        RED,
        "Salt accelerates electron transfer",
        fonts,
        t,
        appear_at=1.8,
        center_x=tk_x,
        y0=870,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "female_explain", center_x=205, bottom_y=1295, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene6_solution_and_outro(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    outro_start = max(3.6, dur - 3.6)

    if t < outro_start:
        # Part 1: Real footage + Animated Protective Coating Shield Diagram
        draw_header_pill(draw, "SOLUTION: BLOCK THE CONTACT!", GREEN, fonts, t)
        bx0, by0, bx1, by1 = 68, 300, 1012, 815
        draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)

        # Iron core safely underneath
        draw.rounded_rectangle([120, 625, 960, 745], radius=18, fill=IRON_GRAY, outline=INK, width=5)
        draw_outlined_text(draw, (540, 685), "IRON SAFE & RUST-FREE ✔", fonts["small_bold"], fill=WHITE, stroke_w=4)

        # Protective paint / zinc galvanize shield layer sweeping across
        shield_p = ease_out_cubic(clamp(t / 1.0))
        sw = int(840 * shield_p)
        if sw > 20:
            draw.rounded_rectangle([120, 540, 120 + sw, 625], radius=16, fill=GREEN, outline=INK, width=5)
            if shield_p > 0.55:
                draw_outlined_text(draw, (540, 582), "PAINT OR ZINC COATING (GALVANIZED)", fonts["small_bold"], fill=WHITE, stroke_w=4)

        # Incoming H2O & O2 particles bouncing OFF the shield!
        for idx, mx in enumerate((250, 440, 640, 830)):
            cyc = (t * 1.15 + idx * 0.25) % 1.0
            # Down from 345 to 500 then bounce back UP!
            if cyc < 0.5:
                my = int(350 + (cyc * 2.0) * 155)
            else:
                my = int(505 - ((cyc - 0.5) * 2.0) * 140)
            col = CYAN if idx % 2 == 0 else RED
            lbl = "H₂O" if idx % 2 == 0 else "O₂"
            draw.ellipse([mx - 28, my - 24, mx + 28, my + 24], fill=col, outline=INK, width=4)
            draw.text((mx, my), lbl, font=fonts["micro_bold"], fill=INK if col == CYAN else WHITE, anchor="mm")
            if 0.42 < cyc < 0.62:
                draw_outlined_text(draw, (mx, 520), "✖", fonts["small_bold"], fill=RED, stroke_w=3)

        # Clean checklist pill below
        ck_x = 370 if mascots else 540
        ck_w = 595 if mascots else 720
        x0 = ck_x - ck_w // 2
        draw_comic_box(draw, (x0, 880, x0 + ck_w, 1020), fill=CARD_BG, radius=24, border_w=5, shadow_offset=8)
        draw.ellipse([x0 + 26, 922, x0 + 82, 978], fill=GREEN, outline=INK, width=4)
        draw.text((x0 + 54, 950), "✔", font=fonts["small_bold"], fill=WHITE, anchor="mm")
        draw.text((x0 + 106, 932), "Paint or Zinc Layer", font=fonts["small_bold"], fill=INK, anchor="lm")
        draw.text((x0 + 106, 972), "Stops water & O₂ reaching iron", font=fonts["tiny_bold"], fill=(70, 78, 96, 255), anchor="lm")

        if mascots:
            mascots.paste(canvas, "male_explain", center_x=875, bottom_y=1295, target_h=430, local_t=t, appear_at=0.15, flip_x=True)

        # Diagonal wipe right into outro
        if t > outro_start - 0.35:
            draw_diagonal_wipe(draw, (t - (outro_start - 0.35)) / 0.35)
    else:
        # Part 2: Signature EXPLAINED! Outro (clean sunburst + card + brand handle + source)
        from src.brand_config import get_brand_handle

        ot = t - outro_start
        burst = draw_sunburst_bg(1080, 1920, angle_offset=ot * 0.25)
        canvas.alpha_composite(burst, (0, 0))
        draw = ImageDraw.Draw(canvas)

        y_shift = 0 if mascots else 185

        # Tilted EXPLAINED! comic card
        p = ease_out_back(ot / 0.35)
        card = Image.new("RGBA", (820, 260), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        draw_comic_box(cd, (20, 20, 790, 230), fill=WHITE, outline=BLUE, radius=32, border_w=10, shadow_offset=12)
        draw_outlined_text(cd, (405, 125), "EXPLAINED!", fonts["terjawab"], fill=BLUE, outline_color=WHITE, stroke_w=0)
        rot = card.rotate(3.5, resample=Image.Resampling.BICUBIC, expand=True)
        rw = max(50, int(rot.width * min(p, 1.03)))
        rh = max(50, int(rot.height * min(p, 1.03)))
        rot_s = rot.resize((rw, rh), Image.Resampling.LANCZOS)
        canvas.alpha_composite(rot_s, (540 - rw // 2, 360 + y_shift - rh // 2))

        draw = ImageDraw.Draw(canvas)
        draw_outlined_text(
            draw,
            (540, 565 + y_shift),
            "Rust = Slow oxidation of iron + water + O₂",
            fonts["summary"],
            fill=WHITE,
            stroke_w=6,
        )

        # Follow brand handle button
        if ot >= 0.45:
            fp = ease_out_back((ot - 0.45) / 0.35)
            bw = int(600 * min(fp, 1.03))
            bh = int(96 * min(fp, 1.03))
            bx = 540 - bw // 2
            by = 665 + y_shift - bh // 2
            draw_comic_box(draw, (bx, by, bx + bw, by + bh), fill=BLUE, outline=WHITE, radius=48, border_w=6, shadow_offset=8)
            draw_outlined_text(draw, (540, 665 + y_shift), f"Follow {get_brand_handle()}", fonts["header"], fill=WHITE, stroke_w=4)

        if mascots:
            mascots.paste(canvas, "male_celebrate", center_x=320, bottom_y=1295, target_h=455, local_t=ot, appear_at=0.10)
            mascots.paste(canvas, "female_celebrate", center_x=760, bottom_y=1295, target_h=445, local_t=ot, appear_at=0.18)

        # Credible scientific source note (matching reference style)
        draw.text(
            (540, 1690),
            "Source: Callister & Rethwisch, Materials Science and Engineering",
            font=fonts["micro_bold"],
            fill=(50, 34, 8, 230),
            anchor="mm",
        )


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "brand": _pick_font(FONT_BOLD_CANDIDATES, 34),
        "header": _pick_font(FONT_BOLD_CANDIDATES, 40),
        "hook_sub": _pick_font(FONT_BOLD_CANDIDATES, 76),
        "hook_main": _pick_font(FONT_BOLD_CANDIDATES, 92),
        "giant_sym": _pick_font(FONT_BOLD_CANDIDATES, 145),
        "big_kw": _pick_font(FONT_BOLD_CANDIDATES, 66),
        "terjawab": _pick_font(FONT_BOLD_CANDIDATES, 108),
        "summary": _pick_font(FONT_BOLD_CANDIDATES, 42),
        "small_bold": _pick_font(FONT_BOLD_CANDIDATES, 32),
        "tiny_bold": _pick_font(FONT_BOLD_CANDIDATES, 24),
        "micro_bold": _pick_font(FONT_BOLD_CANDIDATES, 22),
    }


def _prepare_clean_real_footage_base(
    plan: RenderPlan,
    out_stitched_mp4: Path,
    work_dir: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Stitch clean 1080x1920 real footage background clips (NO static badges — animation overlay handles all visuals!)."""
    seg_dir = work_dir / "hybrid_base_segs"
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg_files: list[Path] = []
    for seg in plan.segments:
        out_seg = seg_dir / f"{seg.scene_id}.mp4"
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},"
            f"eq=contrast=1.05:saturation=1.06:brightness=-0.04,format=yuv420p"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-ss",
                f"{seg.footage_start_offset:.2f}",
                "-i",
                seg.footage_path,
                "-t",
                f"{seg.duration_sec:.3f}",
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "19",
                "-pix_fmt",
                "yuv420p",
                str(out_seg),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        seg_files.append(out_seg)

    concat_txt = seg_dir / "concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_files), encoding="utf-8")
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
            str(out_stitched_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_stitched_mp4


def render_hybrid_korosi_video(
    plan: RenderPlan,
    work_dir: Path,
    output_mp4: Path,
    bgm_path: Optional[str],
    mascots_dir: Optional[Path] = None,
    use_mascot: bool = True,
    stitched_base_mp4: Optional[Path] = None,
    mixed_audio_wav: Optional[Path] = None,
) -> tuple[str, str]:
    """Render 1080x1920 30fps Hybrid Real-Footage + Mechanistic Comic-Science Animation video."""
    width, height, fps = 1080, 1920, 30
    work_dir.mkdir(parents=True, exist_ok=True)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    if stitched_base_mp4 is None or not stitched_base_mp4.exists():
        stitched_base_mp4 = work_dir / "real_footage_base_1080p.mp4"
        _prepare_clean_real_footage_base(plan, stitched_base_mp4, work_dir, width, height, fps)

    if mixed_audio_wav is None or not mixed_audio_wav.exists():
        mixed_audio_wav = work_dir / "mixed_audio_hybrid.wav"
        _mix_audio(plan, bgm_path=bgm_path, out_audio_wav=mixed_audio_wav)

    ass_path = work_dir / "subtitles_hybrid.ass"
    _write_ass_subtitles(plan.subtitles, ass_path, width=width, height=height, font_size_ratio=0.054)

    fonts = _load_fonts()
    mascots = MascotLibrary(mascots_dir) if (use_mascot and mascots_dir) else None

    # Pre-render static lower subtitle scrim once for speed
    bot_scrim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    bsd = ImageDraw.Draw(bot_scrim)
    for y in range(1260, height, 4):
        a = int(175 * ((y - 1260) / (height - 1260)) ** 1.35)
        bsd.rectangle([0, y, width, y + 4], fill=(10, 14, 26, a))

    escaped_ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    filter_complex = (
        f"[0:v][1:v]overlay=0:0:shortest=1,ass='{escaped_ass}',format=yuv420p[vout]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(stitched_base_mp4),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(mixed_audio_wav),
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
        "veryfast",
        "-crf",
        "22",
        "-maxrate",
        "3200k",
        "-bufsize",
        "6400k",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]

    scene_drawers = {
        "scene_1": draw_scene1_hook,
        "scene_2": draw_scene2_oxidation_compare,
        "scene_3": draw_scene3_atomic_mechanism,
        "scene_4": draw_scene4_porous_rust,
        "scene_5": draw_scene5_saltwater_race,
        "scene_6": draw_scene6_solution_and_outro,
    }

    total_frames = int(math.ceil(plan.total_duration_sec * fps))
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None

    segs = plan.segments
    try:
        for f_idx in range(total_frames):
            t_global = f_idx / fps
            # Locate active scene
            seg = segs[-1]
            seg_idx = len(segs) - 1
            for idx, s in enumerate(segs):
                if s.start_sec <= t_global < s.end_sec:
                    seg = s
                    seg_idx = idx
                    break
            local_t = max(0.0, t_global - seg.start_sec)

            canvas = bot_scrim.copy()
            draw = ImageDraw.Draw(canvas)

            drawer = scene_drawers.get(seg.scene_id)
            if drawer:
                drawer(canvas, draw, local_t, seg.duration_sec, fonts, mascots)

            # Diagonal wipe transition at scene boundaries (0.32s duration)
            if seg_idx > 0 and local_t < 0.32:
                draw = ImageDraw.Draw(canvas)
                draw_diagonal_wipe(draw, local_t / 0.32, width, height)

            # Persistent @kimiabill brand pill always cleanly on top
            draw = ImageDraw.Draw(canvas)
            draw_brand_pill(draw, fonts)

            proc.stdin.write(canvas.tobytes())
    finally:
        proc.stdin.close()

    stderr_out = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"FFmpeg hybrid render failed (code {ret}): {stderr_out[-800:]}")

    sheet_path = output_mp4.with_name(f"{output_mp4.stem}_contact_sheet.jpg")
    _build_contact_sheet(output_mp4, plan.segments, sheet_path)
    return str(output_mp4), str(sheet_path)


def _probe_video_dims(video_path: str) -> tuple[int, int]:
    """Return (width, height) of a video file using ffprobe."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                video_path,
            ],
            text=True,
        ).strip()
        w_s, h_s = out.splitlines()[0].split("x")
        return int(w_s), int(h_s)
    except Exception:
        return 1280, 720


def _prepare_smart_real_footage_base(
    plan: RenderPlan,
    out_stitched_mp4: Path,
    work_dir: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Prepare crisp, non-overzoomed real footage base for vertical 9:16 video.

    - Vertical/square clips (aspect <= 1.15): fill 9:16 cleanly with Lanczos scaling.
    - Landscape clips (aspect > 1.15): render a softly blurred dark background fill + crisp
      un-zoomed foreground video window in the upper-middle frame so scientific details stay sharp!
    """
    seg_dir = work_dir / f"smart_base_{width}x{height}"
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg_files: list[Path] = []

    # Landscape foreground window: full frame width, ~33.3% of frame height (1080x640, near 16:9).
    # Sharp and un-zoomed, leaving y=945..1345 completely clean for the animated mechanistic simulation!
    fg_w = width - (width % 2)
    fg_h = int(height * 0.333)
    fg_h -= fg_h % 2
    fg_y = int(height * 0.15)

    for seg in plan.segments:
        out_seg = seg_dir / f"{seg.scene_id}.mp4"
        src_w, src_h = _probe_video_dims(seg.footage_path)
        aspect = (src_w / src_h) if src_h > 0 else 1.77

        if aspect > 1.15:
            # Landscape clip: blurred dark backdrop + sharp, full-frame foreground (NO brutal 3x crop!)
            filter_complex = (
                f"[0:v]split=2[bg0][fg0];"
                f"[bg0]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},gblur=sigma=24,eq=brightness=-0.22:saturation=0.80[bg];"
                f"[fg0]scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={fg_w}:{fg_h},eq=contrast=1.05:saturation=1.08[fg];"
                f"[bg][fg]overlay=(W-w)/2:{fg_y}:shortest=1,fps={fps},format=yuv420p[vout]"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-ss",
                f"{seg.footage_start_offset:.2f}",
                "-i",
                seg.footage_path,
                "-t",
                f"{seg.duration_sec:.3f}",
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
                str(out_seg),
            ]
        else:
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},fps={fps},"
                f"eq=contrast=1.05:saturation=1.06:brightness=-0.03,format=yuv420p"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-ss",
                f"{seg.footage_start_offset:.2f}",
                "-i",
                seg.footage_path,
                "-t",
                f"{seg.duration_sec:.3f}",
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(out_seg),
            ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        seg_files.append(out_seg)

    concat_txt = seg_dir / "concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_files), encoding="utf-8")
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
            str(out_stitched_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_stitched_mp4


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int = 3) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), cand, font=font)
        if (bbox[2] - bbox[0]) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def _draw_discipline_pill(
    draw: ImageDraw.ImageDraw,
    discipline: str,
    fonts: dict,
    section_tag: str = "EVERYDAY_TECH",
) -> None:
    colors = {
        "gadgets": BLUE,
        "engineering": PURPLE,
        "materials": ORANGE,
        "energy": GREEN,
        "biotech": RED,
    }
    col = ORANGE if section_tag == "TECH_RADAR" else colors.get(discipline.lower(), BLUE)
    label = (
        f"TECH RADAR • {discipline.upper()}"
        if section_tag == "TECH_RADAR"
        else f"TECH • {discipline.upper()}"
    )
    bbox = draw.textbbox((0, 0), label, font=fonts["tiny_bold"])
    pw = (bbox[2] - bbox[0]) + 46
    x1 = 1032
    x0 = x1 - pw
    draw_comic_box(draw, (x0, 74, x1, 138), fill=col, radius=24, border_w=4, shadow_offset=5)
    draw_outlined_text(draw, ((x0 + x1) // 2, 106), label, fonts["tiny_bold"], fill=WHITE, stroke_w=3)


def _draw_curved_arrow(
    draw: ImageDraw.ImageDraw,
    p0: tuple[float, float],
    ctrl: tuple[float, float],
    p2: tuple[float, float],
    color: tuple[int, int, int, int],
    width: int = 6,
    steps: int = 24,
) -> list[tuple[float, float]]:
    """Draw a smooth quadratic Bezier curved reaction arrow and return sampled points."""
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        u = i / steps
        x = (1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * ctrl[0] + u ** 2 * p2[0]
        y = (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * ctrl[1] + u ** 2 * p2[1]
        pts.append((x, y))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=color, width=width)
    dx = pts[-1][0] - pts[-3][0]
    dy = pts[-1][1] - pts[-3][1]
    ang = math.atan2(dy, dx)
    head_len = 20
    a1 = (pts[-1][0] - head_len * math.cos(ang - 0.5), pts[-1][1] - head_len * math.sin(ang - 0.5))
    a2 = (pts[-1][0] - head_len * math.cos(ang + 0.5), pts[-1][1] - head_len * math.sin(ang + 0.5))
    draw.polygon([pts[-1], a1, a2], fill=color, outline=INK)
    return pts


def _smart_trim(txt: str, max_chars: int) -> str:
    s = (txt or "").strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rstrip()
    if " " in cut and len(cut.rsplit(" ", 1)[0]) >= 4:
        return cut.rsplit(" ", 1)[0]
    return cut


def _infer_mechanism_params(seg: TimelineSegment, discipline: str = "gadgets") -> dict[str, str]:
    """Return structured mechanism parameters from seg.mechanism or smartly infer from narration/badge."""
    if seg.mechanism is not None:
        m = seg.mechanism
        return {
            "kind": m.kind,
            "title": _smart_trim(m.title or "HOW IT WORKS", 34),
            "actor_a": _smart_trim(m.actor_a or "Input", 16),
            "actor_b": _smart_trim(m.actor_b or "Process", 16),
            "result": _smart_trim(m.result or "Output", 22),
            "caption": _smart_trim(m.caption or "", 54),
        }

    import re
    blob = f"{seg.narration} {seg.overlay_badge or ''} {discipline}".lower()
    if any(k in blob for k in ("polymer", "plastic", "glass", "ceramic", "steel", "foam", "alloy", "coating", "teflon", "material")):
        kind = "polymer_crosslink"
        default_title = "MATERIAL STRUCTURE"
    elif any(k in blob for k in ("electron", "battery", "ion", "charge", "voltage", "current", "coil", "nfc", "oled", "solar", "chip")):
        kind = "electron_transfer"
        default_title = "ELECTRON & CHARGE FLOW"
    elif any(k in blob for k in ("sensor", "blood", "brain", "eye", "melatonin", "filter", "carbon", "biometric")):
        kind = "receptor_binding"
        default_title = "SENSOR & BIO INTERACTION"
    elif any(k in blob for k in ("wave", "sound", "noise", "frequency", "microwave", "heat", "pressure", "wind", "blade", "turbine", "infrared")):
        kind = "force_wave"
        default_title = "ENERGY & WAVE MECHANISM"
    elif any(k in blob for k in ("pixel", "grid", "array", "angle", "geometry", "curve", "pattern", "3d", "dots")):
        kind = "math_geometry"
        default_title = "GEOMETRY & SIGNAL PATTERN"
    else:
        kind = "electron_transfer"
        default_title = "TECH MECHANISM"

    badge = (seg.overlay_badge or "").strip()
    parts = [p.strip() for p in re.split(r"\s*(?:→|->|=>|=|\+)\s*", badge) if p.strip()]
    actor_a = _smart_trim(parts[0], 16) if len(parts) >= 1 else "Input"
    actor_b = _smart_trim(parts[1], 16) if len(parts) >= 2 else "Mechanism"
    result = _smart_trim(parts[-1], 22) if len(parts) >= 3 else (_smart_trim(parts[1], 22) if len(parts) == 2 else "Output")
    return {
        "kind": kind,
        "title": default_title,
        "actor_a": actor_a,
        "actor_b": actor_b,
        "result": result,
        "caption": _smart_trim(badge, 54) if badge else _smart_trim(seg.narration, 50),
    }


def _draw_label_pill(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    bg: tuple[int, int, int, int] = CARD_DARK,
    fg: tuple[int, int, int, int] = WHITE,
    outline: tuple[int, int, int, int] = INK,
    pad_x: int = 16,
    pad_y: int = 8,
    max_w: int = 860,
) -> None:
    """Draw a high-contrast auto-sized rounded pill behind text so labels NEVER spill or truncate needlessly."""
    txt = text.strip()
    if not txt:
        return
    use_font = font
    bbox = draw.textbbox((0, 0), txt, font=use_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # Shrink font down gently before falling back to ellipsis truncation
    if tw + pad_x * 2 > max_w and getattr(font, "path", None) and getattr(font, "size", 0) > 17:
        for sz in (int(font.size) - 3, int(font.size) - 6):
            if sz >= 16:
                try:
                    cand_f = ImageFont.truetype(font.path, sz)
                    bb2 = draw.textbbox((0, 0), txt, font=cand_f)
                    use_font = cand_f
                    tw, th = bb2[2] - bb2[0], bb2[3] - bb2[1]
                    if tw + pad_x * 2 <= max_w:
                        break
                except Exception:
                    pass
    while tw + pad_x * 2 > max_w and len(txt) > 6:
        txt = txt[:-2].rstrip() + "…"
        bbox = draw.textbbox((0, 0), txt, font=use_font)
        tw = bbox[2] - bbox[0]
    pw = min(max_w, tw + pad_x * 2)
    ph = max(34, th + pad_y * 2)
    x0 = cx - pw // 2
    y0 = cy - ph // 2
    x1 = cx + pw // 2
    y1 = cy + ph // 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=ph // 2, fill=bg, outline=outline, width=3)
    draw.text((cx, cy), txt, font=use_font, fill=fg, anchor="mm")


def _anim_polymer_crosslink(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    dur: float,
    params: dict[str, str],
    fonts: dict,
) -> None:
    """Animate polymer chains flexing under bending force -> crosslinks snapping -> rigid lattice!"""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    prog = clamp(t / max(dur * 0.78, 1.8))
    bend_phase = math.sin(min(prog / 0.55, 1.0) * math.pi) if prog < 0.55 else 0.0
    crosslink_prog = clamp((prog - 0.28) / 0.45)
    rigid_prog = clamp((prog - 0.55) / 0.35)

    n_nodes = 7
    row_ys = [y0 + 96, y0 + 154, y0 + 212]
    left_x, right_x = x0 + 95, x1 - 95
    xs = [left_x + i * (right_x - left_x) / (n_nodes - 1) for i in range(n_nodes)]

    if bend_phase > 0.08:
        pulse = int(10 * math.sin(t * 10))
        for fx in (x0 + 42, x1 - 42):
            ay0 = y0 + 20 + pulse
            ay1 = y0 + 76 + pulse
            draw.line([(fx, ay0), (fx, ay1)], fill=RED, width=8)
            draw.polygon([(fx, ay1 + 14), (fx - 13, ay1 - 4), (fx + 13, ay1 - 4)], fill=RED, outline=INK)
        _draw_label_pill(draw, cx, y0 + 26, "BENDING FORCE / STRESS", fonts["micro_bold"], bg=RED, fg=WHITE)
    elif rigid_prog > 0.2:
        _draw_label_pill(draw, cx, y0 + 26, f"✓ {params['result'].upper()}", fonts["micro_bold"], bg=(18, 132, 74, 255), fg=WHITE)

    grid_pts: list[list[tuple[int, int]]] = []
    for r_idx, base_y in enumerate(row_ys):
        row_pts: list[tuple[int, int]] = []
        tighten = int((1 - r_idx) * 12 * rigid_prog)
        for i, gx in enumerate(xs):
            u = (i / (n_nodes - 1)) * 2 - 1
            arch = int((1 - u * u) * 38 * bend_phase)
            jitter = int(math.sin(t * 7 + i + r_idx) * 4 * (1.0 - rigid_prog))
            row_pts.append((int(gx), int(base_y - arch + tighten + jitter)))
        grid_pts.append(row_pts)

    if crosslink_prog > 0.02:
        total_links = n_nodes * 2
        active_links = int(crosslink_prog * total_links)
        link_idx = 0
        for r_idx in range(2):
            for i in range(n_nodes):
                link_idx += 1
                if link_idx <= active_links:
                    p_top = grid_pts[r_idx][i]
                    p_bot = grid_pts[r_idx + 1][i]
                    draw.line([p_top, p_bot], fill=CYAN if rigid_prog < 0.7 else GREEN, width=7)
                    mid_y = (p_top[1] + p_bot[1]) // 2
                    draw.ellipse([p_top[0] - 8, mid_y - 8, p_top[0] + 8, mid_y + 8], fill=YELLOW, outline=INK, width=2)

    chain_col = BLUE if rigid_prog < 0.5 else (20, 120, 95, 255)
    for r_idx, row_pts in enumerate(grid_pts):
        for i in range(len(row_pts) - 1):
            draw.line([row_pts[i], row_pts[i + 1]], fill=INK, width=9)
            draw.line([row_pts[i], row_pts[i + 1]], fill=chain_col, width=5)
        for i, (px, py) in enumerate(row_pts):
            node_col = GREEN if rigid_prog > 0.75 else (ORANGE if bend_phase > 0.35 else BLUE)
            draw.ellipse([px - 14, py - 14, px + 14, py + 14], fill=node_col, outline=INK, width=3)

    # High-contrast progress bar at bottom
    bar_x0, bar_y0, bar_x1, bar_y1 = x0 + 36, y1 - 42, x1 - 36, y1 - 8
    draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, bar_y1], radius=14, fill=CARD_DARK, outline=INK, width=3)
    pct = int(22 + 76 * clamp(crosslink_prog * 0.6 + rigid_prog * 0.4))
    fill_x1 = bar_x0 + int((bar_x1 - bar_x0) * (pct / 100.0))
    bar_col = (210, 95, 20, 255) if pct < 55 else (18, 132, 74, 255)
    draw.rounded_rectangle([bar_x0 + 3, bar_y0 + 3, max(bar_x0 + 24, fill_x1 - 3), bar_y1 - 3], radius=11, fill=bar_col)
    draw_outlined_text(
        draw,
        ((bar_x0 + bar_x1) // 2, (bar_y0 + bar_y1) // 2),
        f"{params['actor_b'].upper()} → STRENGTH / RIGIDITY: {pct}%",
        fonts["micro_bold"],
        fill=WHITE,
        outline_color=INK,
        stroke_w=3,
    )


def _anim_electron_transfer(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    dur: float,
    params: dict[str, str],
    fonts: dict,
) -> None:
    """Animate donor atoms -> curved electron-transfer arrow + flying yellow e⁻ -> acceptor & product."""
    x0, y0, x1, y1 = box
    prog = clamp(t / max(dur * 0.80, 1.6))
    cx = (x0 + x1) // 2
    cy = y0 + 116

    # Left Donor cluster
    donor_x = x0 + 185
    donor_col = RUST_LIGHT if prog > 0.55 else IRON_GRAY
    for dy_off in (-42, 0, 42):
        draw.ellipse([donor_x - 28, cy + dy_off - 24, donor_x + 28, cy + dy_off + 24], fill=donor_col, outline=INK, width=4)
    _draw_label_pill(draw, donor_x, cy, params["actor_a"], fonts["micro_bold"], bg=CARD_DARK, fg=YELLOW, max_w=280)
    _draw_label_pill(draw, donor_x, cy + 82, "DONOR (Oxidation)", fonts["micro_bold"], bg=RUST_DARK, fg=WHITE, max_w=310)

    # Right Acceptor cluster
    acc_x = x1 - 185
    acc_col = (18, 132, 74, 255) if prog > 0.65 else BLUE
    for dy_off in (-42, 0, 42):
        draw.ellipse([acc_x - 28, cy + dy_off - 24, acc_x + 28, cy + dy_off + 24], fill=acc_col, outline=INK, width=4)
    _draw_label_pill(draw, acc_x, cy, params["actor_b"], fonts["micro_bold"], bg=CARD_DARK, fg=CYAN, max_w=280)
    _draw_label_pill(draw, acc_x, cy + 82, "ACCEPTOR (Reduction)", fonts["micro_bold"], bg=BLUE, fg=WHITE, max_w=310)

    # Curved mechanistic reaction arrow arching from Donor -> Acceptor
    p0 = (donor_x + 56, cy - 28)
    ctrl = (cx, y0 + 10)
    p2 = (acc_x - 56, cy - 28)
    arc_pts = _draw_curved_arrow(draw, p0, ctrl, p2, ORANGE, width=7, steps=30)

    # 3 glowing yellow electrons (e⁻) flying along the curved reaction arrow!
    for k in range(3):
        u = (t * 0.65 + k * 0.33) % 1.0
        idx = min(len(arc_pts) - 1, int(u * (len(arc_pts) - 1)))
        ex, ey = arc_pts[idx]
        draw.ellipse([ex - 19, ey - 19, ex + 19, ey + 19], fill=YELLOW, outline=INK, width=3)
        draw.text((ex, ey), "e⁻", font=fonts["micro_bold"], fill=INK, anchor="mm")

    if prog > 0.25:
        _draw_label_pill(
            draw, cx, y1 - 22, f"RESULT: {params['result']}", fonts["tiny_bold"],
            bg=(18, 132, 74, 255), fg=WHITE, max_w=(x1 - x0) - 40,
        )


def _anim_molecular_reaction(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    dur: float,
    params: dict[str, str],
    fonts: dict,
) -> None:
    """Animate two ball-and-stick molecules approaching -> curved arrow -> new covalent bond & product."""
    x0, y0, x1, y1 = box
    prog = clamp(t / max(dur * 0.78, 1.8))
    cx = (x0 + x1) // 2
    cy = y0 + 112

    approach = ease_out_cubic(min(prog / 0.55, 1.0))
    bonded = prog >= 0.55
    ax = int((x0 + 210) + approach * 135)
    bx = int((x1 - 210) - approach * 135)

    # Molecule A (ball-and-stick cluster)
    for ox, oy in ((-46, -34), (-46, 34)):
        draw.line([(ax, cy), (ax + ox, cy + oy)], fill=INK, width=6)
        draw.ellipse([ax + ox - 16, cy + oy - 16, ax + ox + 16, cy + oy + 16], fill=CYAN, outline=INK, width=3)
    draw.ellipse([ax - 34, cy - 34, ax + 34, cy + 34], fill=BLUE, outline=INK, width=4)
    _draw_label_pill(draw, ax - 18, cy + 66, params["actor_a"], fonts["micro_bold"], bg=BLUE, fg=WHITE, max_w=270)

    # Molecule B (ball-and-stick cluster)
    for ox, oy in ((46, -34), (46, 34)):
        draw.line([(bx, cy), (bx + ox, cy + oy)], fill=INK, width=6)
        draw.ellipse([bx + ox - 16, cy + oy - 16, bx + ox + 16, cy + oy + 16], fill=ORANGE, outline=INK, width=3)
    draw.ellipse([bx - 34, cy - 34, bx + 34, cy + 34], fill=PURPLE, outline=INK, width=4)
    _draw_label_pill(draw, bx + 18, cy + 66, params["actor_b"], fonts["micro_bold"], bg=PURPLE, fg=WHITE, max_w=270)

    if not bonded:
        pts = _draw_curved_arrow(draw, (ax + 24, cy - 38), (cx, y0 + 18), (bx - 24, cy - 38), RED, width=6)
        idx = min(len(pts) - 1, int(((t * 1.1) % 1.0) * (len(pts) - 1)))
        ex, ey = pts[idx]
        draw.ellipse([ex - 14, ey - 14, ex + 14, ey + 14], fill=YELLOW, outline=INK, width=3)
    else:
        draw.line([(ax + 34, cy - 7), (bx - 34, cy - 7)], fill=(18, 132, 74, 255), width=8)
        draw.line([(ax + 34, cy + 7), (bx - 34, cy + 7)], fill=(18, 132, 74, 255), width=8)
        r_pulse = int(26 + 14 * math.sin(t * 8))
        draw.ellipse([cx - r_pulse, cy - r_pulse, cx + r_pulse, cy + r_pulse], outline=ORANGE, width=5)

    lbl = f"BOND FORMED: {params['result']}" if bonded else f"{params['actor_a']} + {params['actor_b']} → REACTION"
    _draw_label_pill(
        draw, cx, y1 - 22, lbl, fonts["tiny_bold"],
        bg=(18, 132, 74, 255) if bonded else CARD_DARK, fg=WHITE if bonded else YELLOW,
        max_w=(x1 - x0) - 40,
    )


def _anim_receptor_binding(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    dur: float,
    params: dict[str, str],
    fonts: dict,
) -> None:
    """Animate enzyme/receptor pocket + ligand/drug docking into active site + conformational lock."""
    x0, y0, x1, y1 = box
    prog = clamp(t / max(dur * 0.78, 1.8))
    dock = ease_out_cubic(min(prog / 0.62, 1.0))
    locked = prog >= 0.62
    cx = (x0 + x1) // 2
    cy = y0 + 112

    rx = cx + 80
    rec_col = (18, 132, 74, 255) if locked else PURPLE
    draw.rounded_rectangle([rx - 40, cy - 68, rx + 210, cy + 68], radius=34, fill=rec_col, outline=INK, width=5)
    draw.pieslice([rx - 76, cy - 42, rx + 18, cy + 42], start=300, end=60, fill=CARD_BG, outline=INK, width=4)
    _draw_label_pill(draw, rx + 108, cy, params["actor_b"], fonts["micro_bold"], bg=CARD_DARK, fg=WHITE, max_w=190)

    lx = int((x0 + 175) + dock * (rx - 64 - (x0 + 175)))
    ly = int(cy + math.sin(t * 5) * 12 * (1.0 - dock))
    draw.polygon(
        [(lx - 76, ly - 30), (lx + 16, ly - 30), (lx + 44, ly), (lx + 16, ly + 30), (lx - 76, ly + 30)],
        fill=ORANGE,
        outline=INK,
    )
    _draw_label_pill(draw, lx - 28, ly, params["actor_a"], fonts["micro_bold"], bg=CARD_DARK, fg=YELLOW, max_w=175)

    if locked:
        r_glow = int(46 + 8 * math.sin(t * 9))
        draw.ellipse([rx - 28 - r_glow, cy - r_glow, rx - 28 + r_glow, cy + r_glow], outline=YELLOW, width=5)

    lbl = f"✓ LOCKED: {params['result']}" if locked else f"{params['actor_a']} → TARGETING {params['actor_b']}"
    _draw_label_pill(
        draw, cx, y1 - 22, lbl, fonts["tiny_bold"],
        bg=(18, 132, 74, 255) if locked else CARD_DARK, fg=WHITE if locked else YELLOW,
        max_w=(x1 - x0) - 40,
    )


def _anim_force_wave(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    dur: float,
    params: dict[str, str],
    fonts: dict,
) -> None:
    """Animate live traveling physics wave + oscillating particles + force/energy vectors."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = y0 + 126
    left_x, right_x = x0 + 60, x1 - 60
    draw.line([(left_x, cy), (right_x, cy)], fill=(180, 190, 205, 255), width=3)
    pts: list[tuple[int, int]] = []
    for px in range(left_x, right_x, 8):
        u = (px - left_x) / max(1, right_x - left_x)
        py = int(cy - 54 * math.sin(u * 3.5 * math.pi - t * 6.5))
        pts.append((px, py))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=BLUE, width=7)
    for k in range(8):
        u = k / 7.0
        px = int(left_x + u * (right_x - left_x))
        dy = int(-54 * math.sin(u * 3.5 * math.pi - t * 6.5))
        py = cy + dy
        draw.line([(px, cy), (px, py)], fill=ORANGE, width=4)
        draw.ellipse([px - 12, py - 12, px + 12, py + 12], fill=YELLOW, outline=INK, width=3)

    _draw_label_pill(draw, left_x + 120, y0 + 26, params["actor_a"], fonts["micro_bold"], bg=BLUE, fg=WHITE, max_w=260)
    _draw_label_pill(draw, right_x - 120, y0 + 26, params["actor_b"], fonts["micro_bold"], bg=ORANGE, fg=WHITE, max_w=260)
    _draw_label_pill(draw, cx, y1 - 22, f"EFFECT: {params['result']}", fonts["tiny_bold"], bg=BLUE, fg=WHITE, max_w=(x1 - x0) - 40)


def _anim_math_geometry(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    dur: float,
    params: dict[str, str],
    fonts: dict,
) -> None:
    """Animate live mathematical coordinate curve drawing itself with moving glowing point & tangent."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    prog = clamp(t / max(dur * 0.82, 1.8))
    gx0, gy0, gx1, gy1 = x0 + 80, y0 + 30, x1 - 80, y1 - 58
    for i in range(1, 5):
        gy = gy0 + i * (gy1 - gy0) // 5
        draw.line([(gx0, gy), (gx1, gy)], fill=(225, 232, 240, 255), width=2)
    draw.line([(gx0, gy1), (gx1, gy1)], fill=INK, width=5)
    draw.line([(gx0, gy1), (gx0, gy0)], fill=INK, width=5)
    steps = max(2, int(prog * 48))
    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        u = i / 48.0
        px = int(gx0 + u * (gx1 - gx0))
        val = u ** 2.1
        py = int(gy1 - val * (gy1 - gy0 - 12))
        pts.append((px, py))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=PURPLE, width=7)
    lx, ly = pts[-1]
    draw.ellipse([lx - 14, ly - 14, lx + 14, ly + 14], fill=YELLOW, outline=INK, width=3)
    _draw_label_pill(draw, gx0 + 130, gy0 + 22, params["actor_a"], fonts["micro_bold"], bg=PURPLE, fg=WHITE, max_w=260)
    _draw_label_pill(draw, cx, y1 - 22, f"RESULT: {params['result']}", fonts["tiny_bold"], bg=PURPLE, fg=WHITE, max_w=(x1 - x0) - 40)


def _draw_mechanistic_diagram_card(
    draw: ImageDraw.ImageDraw,
    seg: TimelineSegment,
    seg_idx: int,
    local_t: float,
    fonts: dict,
    discipline: str = "gadgets",
) -> None:
    """Draw a genuine 30fps comic-science mechanistic visual simulation (atoms, electrons, chains, receptors, waves, graphs)."""
    p = ease_out_back(local_t / 0.35)
    if p <= 0.05:
        return

    card_x0, card_y0, card_x1, card_y1 = 56, 910, 1024, 1275
    cy = (card_y0 + card_y1) // 2
    half_h = int(((card_y1 - card_y0) // 2) * min(p, 1.02))
    draw_comic_box(
        draw,
        (card_x0, cy - half_h, card_x1, cy + half_h),
        fill=CARD_BG,
        outline=INK,
        radius=28,
        border_w=6,
        shadow_offset=10,
    )
    if p < 0.65:
        return

    params = _infer_mechanism_params(seg, discipline=discipline)
    # Header bar inside simulation box
    draw.rounded_rectangle([card_x0 + 18, card_y0 + 14, card_x1 - 18, card_y0 + 62], radius=16, fill=CARD_DARK)
    draw.text(
        ((card_x0 + card_x1) // 2, card_y0 + 38),
        f"MECHANISM SIMULATION • {params['title'].upper()}",
        font=fonts["tiny_bold"],
        fill=YELLOW,
        anchor="mm",
    )

    sim_box = (card_x0 + 18, card_y0 + 68, card_x1 - 18, card_y1 - 14)
    kind = params["kind"]
    if kind == "polymer_crosslink":
        _anim_polymer_crosslink(draw, sim_box, local_t, seg.duration_sec, params, fonts)
    elif kind == "electron_transfer":
        _anim_electron_transfer(draw, sim_box, local_t, seg.duration_sec, params, fonts)
    elif kind == "receptor_binding":
        _anim_receptor_binding(draw, sim_box, local_t, seg.duration_sec, params, fonts)
    elif kind == "force_wave":
        _anim_force_wave(draw, sim_box, local_t, seg.duration_sec, params, fonts)
    elif kind == "math_geometry":
        _anim_math_geometry(draw, sim_box, local_t, seg.duration_sec, params, fonts)
    else:
        _anim_molecular_reaction(draw, sim_box, local_t, seg.duration_sec, params, fonts)


def _draw_references_card(
    draw: ImageDraw.ImageDraw,
    references: list[str],
    fonts: dict,
    local_t: float,
    appear_at: float,
    requested_by: Optional[str] = None,
) -> None:
    """Draw animated 'THANK YOU FOR WATCHING!' + Follow @handle CTA + Sources & References end-card."""
    if local_t < appear_at:
        return
    ot = local_t - appear_at
    p = ease_out_back(ot / 0.35)
    if p <= 0.05:
        return

    x0, y0, x1, y1 = 56, 910, 1024, 1320
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    hh = int(((y1 - y0) // 2) * min(p, 1.02))
    draw_comic_box(draw, (x0, cy - hh, x1, cy + hh), fill=CARD_DARK, outline=YELLOW, radius=28, border_w=6, shadow_offset=10)
    if p < 0.65:
        return

    # 1. Animated "THANK YOU FOR WATCHING!" banner with twinkling sparkles
    bob = int(4 * math.sin(ot * 6.0))
    draw_outlined_text(
        draw,
        (cx, y0 + 44 + bob),
        "THANK YOU FOR WATCHING!",
        fonts["summary"],
        fill=YELLOW,
        outline_color=INK,
        stroke_w=5,
    )
    # Animated star/sparkle dots flanking the title
    for side in (-1, 1):
        sx = cx + side * 375
        sy = y0 + 44 + int(6 * math.cos(ot * 7.0 + side))
        sr = 8 + int(4 * math.sin(ot * 9.0 + side))
        draw.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=CYAN, outline=WHITE, width=2)

    # 2. Animated pulsing Follow & Comment-Request CTA pill
    cta_pulse = int(3 * math.sin(ot * 8.0))
    handle_upper = get_brand_handle().upper()
    cta_txt = (
        f"★ THANKS {requested_by[:16].upper()}! DROP MORE REQUESTS! ★"
        if requested_by
        else f"★ FOLLOW {handle_upper} • REQUEST TOPICS BELOW! ★"
    )
    _draw_label_pill(
        draw,
        cx,
        y0 + 106 - cta_pulse,
        cta_txt,
        fonts["tiny_bold"],
        bg=RED,
        fg=WHITE,
        outline=YELLOW,
        pad_x=22,
        pad_y=10,
        max_w=(x1 - x0) - 48,
    )

    # 3. Divider + Sources & References bibliography
    draw.line([(x0 + 34, y0 + 148), (x1 - 34, y0 + 148)], fill=(255, 255, 255, 95), width=2)
    draw.text((x0 + 34, y0 + 162), "SOURCES & REFERENCES:", font=fonts["tiny_bold"], fill=YELLOW, anchor="lt")

    ty = y0 + 198
    refs_to_show = references[:3] if references else [f"{get_brand_name()} Editorial & Wikimedia Commons (CC)"]
    for idx, ref in enumerate(refs_to_show, start=1):
        wrapped = _wrap_text(draw, f"{idx}. {ref}", fonts["micro_bold"], (x1 - x0) - 72, max_lines=2)
        for ln in wrapped:
            if ty + 24 > y1 - 14:
                break
            draw.text((x0 + 36, ty), ln, font=fonts["micro_bold"], fill=WHITE, anchor="lt")
            ty += 26
        ty += 4


def render_hybrid_science_video(
    plan: RenderPlan,
    work_dir: Path,
    output_mp4: Path,
    bgm_path: Optional[str],
    mascots_dir: Optional[Path] = None,
    use_mascot: bool = True,
) -> tuple[str, str]:
    """Universal hybrid comic-science renderer (works for ANY pure-science topic & both 480p proxy / 1080p HD)."""
    is_final = plan.resolution_mode == "final_1080p"
    out_w, out_h, fps = (1080, 1920, 30) if is_final else (480, 854, 15)
    canvas_w, canvas_h = 1080, 1920

    work_dir.mkdir(parents=True, exist_ok=True)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    stitched_base_mp4 = work_dir / f"smart_base_{out_w}x{out_h}_v{plan.version}.mp4"
    _prepare_smart_real_footage_base(plan, stitched_base_mp4, work_dir, out_w, out_h, fps)

    mixed_audio_wav = work_dir / f"mixed_audio_v{plan.version}.wav"
    _mix_audio(plan, bgm_path=bgm_path, out_audio_wav=mixed_audio_wav)

    ass_path = work_dir / f"subtitles_hybrid_v{plan.version}.ass"
    _write_ass_subtitles(
        plan.subtitles,
        ass_path,
        width=out_w,
        height=out_h,
        font_size_ratio=plan.subtitle_font_size_ratio,
    )

    fonts = _load_fonts()
    mascots = MascotLibrary(mascots_dir) if (use_mascot and plan.use_mascot and mascots_dir) else None

    cover_overlay_img: Optional[Image.Image] = None
    cover_path_str = getattr(plan, "cover_thumbnail_path", None)
    if cover_path_str and Path(cover_path_str).exists():
        try:
            cover_overlay_img = (
                Image.open(cover_path_str).convert("RGBA").resize((canvas_w, canvas_h), Image.Resampling.LANCZOS)
            )
        except Exception:
            cover_overlay_img = None

    # Pre-render bottom gradient scrim at 1080x1920
    bot_scrim = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    bsd = ImageDraw.Draw(bot_scrim)
    for y in range(1240, canvas_h, 4):
        a = int(190 * ((y - 1240) / (canvas_h - 1240)) ** 1.3)
        bsd.rectangle([0, y, canvas_w, y + 4], fill=(10, 14, 26, a))

    escaped_ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    if is_final:
        filter_complex = f"[0:v][1:v]overlay=0:0:shortest=1,ass='{escaped_ass}',format=yuv420p[vout]"
    else:
        filter_complex = (
            f"[1:v]scale={out_w}:{out_h}:flags=bilinear[ov];"
            f"[0:v][ov]overlay=0:0:shortest=1,ass='{escaped_ass}',format=yuv420p[vout]"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(stitched_base_mp4),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{canvas_w}x{canvas_h}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-i",
        str(mixed_audio_wav),
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
        "medium" if is_final else "veryfast",
        "-crf",
        "22" if is_final else "24",
        *(["-maxrate", "3200k", "-bufsize", "6400k"] if is_final else []),
        "-c:a",
        "aac",
        "-b:a",
        "160k" if is_final else "128k",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]

    total_frames = int(math.ceil(plan.total_duration_sec * fps))
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None

    segs = plan.segments
    num_segs = len(segs)
    header_colors = [RED, BLUE, PURPLE, ORANGE, GREEN, BLUE]
    wm_pill_icon, wm_corner_stamp = _load_watermark_badges()

    try:
        for f_idx in range(total_frames):
            t_global = f_idx / fps
            if cover_overlay_img is not None and t_global < 0.32:
                proc.stdin.write(cover_overlay_img.tobytes())
                continue

            seg = segs[-1]
            seg_idx = num_segs - 1
            for idx, s in enumerate(segs):
                if s.start_sec <= t_global < s.end_sec:
                    seg = s
                    seg_idx = idx
                    break
            local_t = max(0.0, t_global - seg.start_sec)

            canvas = bot_scrim.copy()
            draw = ImageDraw.Draw(canvas)

            is_first = seg_idx == 0
            is_last = seg_idx == num_segs - 1

            if is_first:
                sec_tag = getattr(plan, "section_tag", "EVERYDAY_TECH")
                is_radar = sec_tag == "TECH_RADAR"
                req_by = getattr(plan, "requested_by", None)
                # Ensure frame 0 (t=0.0) already displays a full-contrast visual cover hook
                cover_t = local_t + 0.45
                draw_header_pill(
                    draw,
                    "TECH RADAR • LATEST BREAKTHROUGH" if is_radar else ("VIEWER REQUEST!" if req_by else "HOW DOES THIS WORK?!"),
                    ORANGE if is_radar else RED,
                    fonts,
                    cover_t,
                    y_center=225,
                )
                if req_by:
                    _draw_label_pill(
                        draw,
                        540,
                        952,
                        f"★ REQUESTED BY: {req_by[:28]} ★",
                        fonts["tiny_bold"],
                        bg=CYAN,
                        fg=INK,
                        outline=WHITE,
                        pad_x=24,
                        pad_y=8,
                        max_w=860,
                    )
                if plan.title:
                    # Show crisp, bold curiosity title prominently right from frame 0
                    box_y0, box_y1 = 990, 1260
                    draw_comic_box(draw, (80, box_y0, 1000, box_y1), fill=CARD_BG, outline=INK, radius=28, border_w=6, shadow_offset=10)
                    eyebrow = (
                        "WEEKEND • TECH RADAR"
                        if is_radar
                        else (f"REQUEST • {req_by[:20]}" if req_by else f"EVERYDAY TECH • {plan.discipline.upper()}")
                    )
                    _draw_label_pill(
                        draw,
                        540,
                        box_y0 + 44,
                        eyebrow,
                        fonts["tiny_bold"],
                        bg=ORANGE if is_radar else BLUE,
                        fg=WHITE,
                        outline=INK,
                        pad_x=24,
                        pad_y=7,
                        max_w=780,
                    )
                    title_font = fonts["header"]
                    t_lines = _wrap_text(draw, plan.title, title_font, 840, max_lines=3)
                    line_step = 50
                    ty = box_y0 + 105 + max(0, (3 - len(t_lines)) * 18)
                    for ln in t_lines:
                        draw.text((540, ty), ln, font=title_font, fill=INK, anchor="mm")
                        ty += line_step
                if mascots:
                    mascots.paste(canvas, "male_curious", center_x=135, bottom_y=1530, target_h=220, local_t=cover_t, appear_at=0.0)
                    mascots.paste(canvas, "female_curious", center_x=945, bottom_y=1530, target_h=220, local_t=cover_t, appear_at=0.0)
            elif is_last:
                # Final scene: ANSWERED! + celebrating duo mascots + Thank You / Follow / Sources end-card
                outro_start = max(0.15, seg.duration_sec - 4.5)
                draw_header_pill(draw, "ANSWERED!", GREEN, fonts, local_t, y_center=225)
                _draw_references_card(
                    draw,
                    plan.references,
                    fonts,
                    local_t,
                    appear_at=outro_start,
                    requested_by=getattr(plan, "requested_by", None),
                )
                if mascots:
                    mascots.paste(canvas, "male_celebrate", center_x=170, bottom_y=960, target_h=235, local_t=local_t, appear_at=0.1)
                    mascots.paste(canvas, "female_celebrate", center_x=910, bottom_y=960, target_h=235, local_t=local_t, appear_at=0.2)
            else:
                # Middle scenes: Genuine 30fps mechanistic simulation + alternating mascot
                h_col = header_colors[seg_idx % len(header_colors)]
                draw_header_pill(draw, f"STEP {seg_idx} • HOW IT WORKS", h_col, fonts, local_t, y_center=225)
                _draw_mechanistic_diagram_card(draw, seg, seg_idx, local_t, fonts, discipline=plan.discipline)
                if mascots:
                    if seg_idx % 2 == 1:
                        mascots.paste(canvas, "male_explain", center_x=135, bottom_y=1530, target_h=215, local_t=local_t, appear_at=0.12)
                    else:
                        mascots.paste(canvas, "female_explain", center_x=945, bottom_y=1530, target_h=215, local_t=local_t, appear_at=0.12)

            # Diagonal comic wipe transition between scenes
            if seg_idx > 0 and local_t < 0.30:
                draw = ImageDraw.Draw(canvas)
                draw_diagonal_wipe(draw, local_t / 0.30, canvas_w, canvas_h)

            # Persistent top brand + Mascot watermark & discipline pills
            draw = ImageDraw.Draw(canvas)
            draw_brand_pill(draw, fonts)
            draw_mascot_watermark(canvas, wm_pill_icon, wm_corner_stamp)
            _draw_discipline_pill(draw, plan.discipline, fonts, section_tag=getattr(plan, "section_tag", "EVERYDAY_TECH"))

            if cover_overlay_img is not None and 0.32 <= t_global < 0.45:
                fade_alpha = max(0.0, min(1.0, (0.45 - t_global) / 0.13))
                canvas = Image.blend(canvas, cover_overlay_img, fade_alpha)

            proc.stdin.write(canvas.tobytes())
    finally:
        proc.stdin.close()

    stderr_out = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"FFmpeg hybrid science render failed (code {ret}): {stderr_out[-800:]}")

    sheet_path = output_mp4.with_name(f"{output_mp4.stem}_contact_sheet.jpg")
    _build_contact_sheet(output_mp4, plan.segments, sheet_path)
    return str(output_mp4), str(sheet_path)

