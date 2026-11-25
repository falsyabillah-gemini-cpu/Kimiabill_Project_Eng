"""Generate nerdy blue-haired tech-geek mascot sprites (placeholder flat-vector)
and dynamically composite all channel branding assets from `config/brand.yaml`:
  - `assets/watermark.png` (512x512 Shorts overlay & corner stamp)
  - `assets/youtube_branding_watermark.png` (512x512 YouTube Studio player watermark)
  - `assets/profile_picture.png` (800x800 YouTube circular avatar)
  - `assets/channel_banner.png` (2560x1440 YouTube banner with 1546x423 safe zone)

Usage:
  python -m src.tools.brand_assets                  # Generate mascots (if missing) + watermark
  python -m src.tools.brand_assets --watermark-only # Rebuild only assets/watermark.png
  python -m src.tools.brand_assets --branding-kit   # Rebuild watermark, YouTube watermark, profile picture, and banner
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.brand_config import (
    BASE_DIR,
    clear_brand_cache,
    get_brand_handle,
    get_brand_name,
    get_brand_tagline,
)

MASCOTS_DIR = BASE_DIR / "assets" / "mascots"
WATERMARK_PATH = BASE_DIR / "assets" / "watermark.png"
YT_WATERMARK_PATH = BASE_DIR / "assets" / "youtube_branding_watermark.png"
PROFILE_PIC_PATH = BASE_DIR / "assets" / "profile_picture.png"
BANNER_PATH = BASE_DIR / "assets" / "channel_banner.png"

INK = (16, 22, 38, 255)
HAIR_BLUE = (34, 118, 245, 255)
HAIR_HI = (92, 178, 255, 255)
HAIR_SHADOW = (20, 74, 178, 255)
SKIN = (255, 214, 184, 255)
SKIN_SHADE = (240, 184, 152, 255)
HOODIE_DARK = (28, 36, 62, 255)
HOODIE_MID = (44, 56, 94, 255)
CYAN = (42, 224, 255, 255)
GOLD = (255, 208, 48, 255)
PANTS = (26, 30, 44, 255)
WHITE = (255, 255, 255, 255)
GLASS_TINT = (180, 238, 255, 110)


def _pick_bold_font(size: int) -> ImageFont.FreeTypeFont:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ):
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _pick_mono_bold_font(size: int) -> ImageFont.FreeTypeFont:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    ):
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return _pick_bold_font(size)


def _draw_tech_geek_character(
    gender: str = "male",
    pose: str = "explain",
    scale: int = 2,
) -> Image.Image:
    """Render a crisp 2x-supersampled flat-vector nerdy blue-haired tech-geek mascot."""
    w, h = 420 * scale, 720 * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = scale
    ow = 6 * s

    cx = 210 * s

    # --- Legs & Tech Sneakers ---
    d.rounded_rectangle([cx - 56 * s, 455 * s, cx - 12 * s, 635 * s], radius=16 * s, fill=PANTS, outline=INK, width=ow)
    d.rounded_rectangle([cx + 12 * s, 455 * s, cx + 56 * s, 635 * s], radius=16 * s, fill=PANTS, outline=INK, width=ow)
    # Shoes
    d.rounded_rectangle([cx - 74 * s, 622 * s, cx - 8 * s, 668 * s], radius=18 * s, fill=WHITE, outline=INK, width=ow)
    d.rounded_rectangle([cx - 70 * s, 646 * s, cx - 10 * s, 668 * s], radius=10 * s, fill=CYAN, outline=INK, width=4 * s)
    d.rounded_rectangle([cx + 8 * s, 622 * s, cx + 74 * s, 668 * s], radius=18 * s, fill=WHITE, outline=INK, width=ow)
    d.rounded_rectangle([cx + 10 * s, 646 * s, cx + 70 * s, 668 * s], radius=10 * s, fill=CYAN, outline=INK, width=4 * s)

    # --- Torso: Dark Tech Hoodie with glowing cyan microchip ---
    torso_box = [cx - 78 * s, 295 * s, cx + 78 * s, 475 * s]
    d.rounded_rectangle(torso_box, radius=32 * s, fill=HOODIE_DARK, outline=INK, width=ow)
    # Hoodie zipper line & pocket
    d.line([(cx, 315 * s), (cx, 472 * s)], fill=HOODIE_MID, width=4 * s)
    d.rounded_rectangle([cx - 48 * s, 405 * s, cx + 48 * s, 458 * s], radius=14 * s, fill=HOODIE_MID, outline=INK, width=4 * s)

    # Glowing Microchip Emblem on chest
    chip_cx, chip_cy = cx - (28 * s if gender == "male" else -28 * s), 358 * s
    for dx in (-10 * s, 0, 10 * s):
        d.line([(chip_cx + dx, chip_cy - 22 * s), (chip_cx + dx, chip_cy + 22 * s)], fill=CYAN, width=3 * s)
        d.line([(chip_cx - 22 * s, chip_cy + dx), (chip_cx + 22 * s, chip_cy + dx)], fill=CYAN, width=3 * s)
    d.rounded_rectangle(
        [chip_cx - 15 * s, chip_cy - 15 * s, chip_cx + 15 * s, chip_cy + 15 * s],
        radius=5 * s,
        fill=HOODIE_DARK,
        outline=CYAN,
        width=4 * s,
    )
    d.ellipse([chip_cx - 5 * s, chip_cy - 5 * s, chip_cx + 5 * s, chip_cy + 5 * s], fill=CYAN)

    # --- Arms per pose ---
    if pose == "curious":
        # Left arm resting, right hand raised to chin/glasses
        d.line([(cx - 72 * s, 325 * s), (cx - 102 * s, 415 * s), (cx - 64 * s, 438 * s)], fill=HOODIE_DARK, width=34 * s, joint="curve")
        d.ellipse([cx - 78 * s, 420 * s, cx - 44 * s, 454 * s], fill=SKIN, outline=INK, width=ow)
        d.line([(cx + 72 * s, 325 * s), (cx + 108 * s, 365 * s), (cx + 42 * s, 272 * s)], fill=HOODIE_DARK, width=34 * s, joint="curve")
        d.ellipse([cx + 22 * s, 254 * s, cx + 56 * s, 288 * s], fill=SKIN, outline=INK, width=ow)
    elif pose == "celebrate":
        # Both arms raised excitedly
        d.line([(cx - 72 * s, 322 * s), (cx - 125 * s, 255 * s), (cx - 115 * s, 200 * s)], fill=HOODIE_DARK, width=34 * s, joint="curve")
        d.ellipse([cx - 134 * s, 176 * s, cx - 96 * s, 214 * s], fill=SKIN, outline=INK, width=ow)
        d.line([(cx + 72 * s, 322 * s), (cx + 125 * s, 255 * s), (cx + 115 * s, 200 * s)], fill=HOODIE_DARK, width=34 * s, joint="curve")
        d.ellipse([cx + 96 * s, 176 * s, cx + 134 * s, 214 * s], fill=SKIN, outline=INK, width=ow)
    else:
        # Explain pose: left hand on hip/front, right hand gesturing outward
        d.line([(cx - 72 * s, 325 * s), (cx - 98 * s, 398 * s), (cx - 48 * s, 412 * s)], fill=HOODIE_DARK, width=34 * s, joint="curve")
        d.ellipse([cx - 60 * s, 394 * s, cx - 26 * s, 428 * s], fill=SKIN, outline=INK, width=ow)
        d.line([(cx + 72 * s, 325 * s), (cx + 122 * s, 330 * s), (cx + 135 * s, 282 * s)], fill=HOODIE_DARK, width=34 * s, joint="curve")
        d.ellipse([cx + 118 * s, 260 * s, cx + 154 * s, 296 * s], fill=SKIN, outline=INK, width=ow)

    # --- Over-ear Headphones around neck ---
    d.arc([cx - 58 * s, 272 * s, cx + 58 * s, 342 * s], start=0, end=180, fill=HOODIE_MID, width=10 * s)
    d.rounded_rectangle([cx - 74 * s, 294 * s, cx - 44 * s, 336 * s], radius=10 * s, fill=CYAN, outline=INK, width=4 * s)
    d.rounded_rectangle([cx + 44 * s, 294 * s, cx + 74 * s, 336 * s], radius=10 * s, fill=CYAN, outline=INK, width=4 * s)

    # --- Neck ---
    d.rectangle([cx - 18 * s, 265 * s, cx + 18 * s, 305 * s], fill=SKIN_SHADE, outline=INK, width=ow)

    # --- Back Hair (if female, shoulder-length cobalt hair) ---
    if gender == "female":
        d.rounded_rectangle([cx - 110 * s, 118 * s, cx + 110 * s, 298 * s], radius=68 * s, fill=HAIR_SHADOW, outline=INK, width=ow)

    # --- Ears & Face ---
    d.ellipse([cx - 104 * s, 178 * s, cx - 74 * s, 224 * s], fill=SKIN, outline=INK, width=ow)
    d.ellipse([cx + 74 * s, 178 * s, cx + 104 * s, 224 * s], fill=SKIN, outline=INK, width=ow)
    d.ellipse([cx - 88 * s, 108 * s, cx + 88 * s, 280 * s], fill=SKIN, outline=INK, width=ow)

    # --- Eyes & Mouth ---
    for ex in (cx - 34 * s, cx + 34 * s):
        d.ellipse([ex - 16 * s, 178 * s, ex + 16 * s, 214 * s], fill=WHITE, outline=INK, width=4 * s)
        d.ellipse([ex - 9 * s, 184 * s, ex + 9 * s, 208 * s], fill=HAIR_BLUE)
        d.ellipse([ex - 4 * s, 189 * s, ex + 4 * s, 201 * s], fill=INK)
        d.ellipse([ex - 8 * s, 184 * s, ex - 2 * s, 191 * s], fill=WHITE)

    # Eyebrows
    d.arc([cx - 56 * s, 152 * s, cx - 12 * s, 176 * s], start=195, end=345, fill=HAIR_SHADOW, width=6 * s)
    d.arc([cx + 12 * s, 152 * s, cx + 56 * s, 176 * s], start=195, end=345, fill=HAIR_SHADOW, width=6 * s)

    # Mouth per pose
    if pose == "curious":
        d.ellipse([cx - 12 * s, 232 * s, cx + 12 * s, 256 * s], fill=(196, 68, 86, 255), outline=INK, width=4 * s)
    elif pose == "celebrate":
        d.chord([cx - 28 * s, 222 * s, cx + 28 * s, 264 * s], start=0, end=180, fill=(210, 70, 92, 255), outline=INK, width=4 * s)
    else:
        d.chord([cx - 24 * s, 224 * s, cx + 24 * s, 258 * s], start=0, end=180, fill=(210, 70, 92, 255), outline=INK, width=4 * s)

    # --- Thick Nerdy Tech Glasses ---
    d.rounded_rectangle([cx - 72 * s, 168 * s, cx - 8 * s, 220 * s], radius=12 * s, fill=GLASS_TINT, outline=INK, width=7 * s)
    d.rounded_rectangle([cx + 8 * s, 168 * s, cx + 72 * s, 220 * s], radius=12 * s, fill=GLASS_TINT, outline=INK, width=7 * s)
    d.line([(cx - 8 * s, 192 * s), (cx + 8 * s, 192 * s)], fill=INK, width=7 * s)
    d.line([(cx - 86 * s, 190 * s), (cx - 72 * s, 190 * s)], fill=INK, width=5 * s)
    d.line([(cx + 72 * s, 190 * s), (cx + 86 * s, 190 * s)], fill=INK, width=5 * s)
    # Lens glare
    d.line([(cx - 60 * s, 178 * s), (cx - 46 * s, 192 * s)], fill=WHITE, width=3 * s)
    d.line([(cx + 20 * s, 178 * s), (cx + 34 * s, 192 * s)], fill=WHITE, width=3 * s)

    # --- Vibrant Blue Front Hair / Spikes ---
    if gender == "male":
        spikes = [
            (cx - 96 * s, 168 * s),
            (cx - 108 * s, 108 * s),
            (cx - 72 * s, 72 * s),
            (cx - 24 * s, 44 * s),
            (cx + 16 * s, 54 * s),
            (cx + 68 * s, 42 * s),
            (cx + 96 * s, 96 * s),
            (cx + 104 * s, 165 * s),
            (cx + 70 * s, 146 * s),
            (cx + 32 * s, 164 * s),
            (cx - 4 * s, 138 * s),
            (cx - 44 * s, 162 * s),
            (cx - 76 * s, 144 * s),
        ]
        d.polygon(spikes, fill=HAIR_BLUE, outline=INK, width=ow)
        d.polygon([(cx - 42 * s, 82 * s), (cx - 10 * s, 68 * s), (cx + 26 * s, 84 * s), (cx - 12 * s, 102 * s)], fill=HAIR_HI)
    else:
        bangs = [
            (cx - 100 * s, 175 * s),
            (cx - 96 * s, 84 * s),
            (cx - 32 * s, 52 * s),
            (cx + 40 * s, 56 * s),
            (cx + 98 * s, 94 * s),
            (cx + 102 * s, 176 * s),
            (cx + 64 * s, 146 * s),
            (cx + 18 * s, 162 * s),
            (cx - 18 * s, 136 * s),
            (cx - 64 * s, 160 * s),
        ]
        d.polygon(bangs, fill=HAIR_BLUE, outline=INK, width=ow)
        # Glowing cyan circuit hairclip
        d.rounded_rectangle([cx + 48 * s, 118 * s, cx + 78 * s, 134 * s], radius=6 * s, fill=CYAN, outline=INK, width=4 * s)

    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    return img.resize((img.width // scale, img.height // scale), Image.Resampling.LANCZOS)


def generate_placeholder_mascots(force: bool = False) -> list[Path]:
    MASCOTS_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for gender in ("male", "female"):
        for pose in ("curious", "explain", "celebrate"):
            out = MASCOTS_DIR / f"{gender}_{pose}.png"
            if out.exists() and not force:
                continue
            im = _draw_tech_geek_character(gender=gender, pose=pose, scale=2)
            im.save(out)
            created.append(out)
    return created


def _draw_microchip_emblem(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: tuple[int, int, int, int]) -> None:
    half = size // 2
    pin_len = max(6, size // 4)
    pw = max(2, size // 18)
    for offset in (-half // 2, 0, half // 2):
        draw.line([(cx + offset, cy - half - pin_len), (cx + offset, cy + half + pin_len)], fill=color, width=pw)
        draw.line([(cx - half - pin_len, cy + offset), (cx + half + pin_len, cy + offset)], fill=color, width=pw)
    draw.rounded_rectangle(
        [cx - half, cy - half, cx + half, cy + half],
        radius=max(4, size // 6),
        fill=(12, 20, 42, 255),
        outline=color,
        width=max(3, size // 12),
    )
    inner = half // 2
    draw.rounded_rectangle(
        [cx - inner, cy - inner, cx + inner, cy + inner],
        radius=max(2, size // 12),
        fill=color,
    )


def _draw_circuit_backdrop(width: int, height: int) -> Image.Image:
    """Create a rich dark-navy cyberpunk/sci-tech backdrop with radial glows, hex grid & circuit paths."""
    base = Image.new("RGBA", (width, height), (8, 14, 30, 255))

    # Diagonal subtle cobalt gradient
    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    steps = 48
    for i in range(steps):
        t = i / max(1, steps - 1)
        y0 = int(height * t)
        y1 = int(height * ((i + 1) / steps)) + 1
        r = int(8 + 10 * math.sin(t * math.pi))
        g = int(14 + 20 * math.sin(t * math.pi))
        b = int(32 + 34 * math.sin(t * math.pi))
        gd.rectangle([0, y0, width, y1], fill=(r, g, b, 255))
    base = Image.alpha_composite(base, grad)

    # Soft cyan & deep-blue ambient radial blooms
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gld = ImageDraw.Draw(glow)
    cx, cy = width // 2, height // 2
    r_main = int(min(width, height) * 0.48)
    gld.ellipse([cx - r_main, cy - r_main, cx + r_main, cy + r_main], fill=(24, 156, 235, 78))
    gld.ellipse([int(width * 0.14), int(height * 0.16), int(width * 0.44), int(height * 0.84)], fill=(38, 195, 255, 52))
    gld.ellipse([int(width * 0.56), int(height * 0.16), int(width * 0.86), int(height * 0.84)], fill=(255, 196, 48, 34))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(24, min(width, height) // 14)))
    base = Image.alpha_composite(base, glow)

    # Subtle cyber grid & hex dots
    grid = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dr = ImageDraw.Draw(grid)
    step = max(44, min(width, height) // 14)
    for x in range(0, width, step):
        dr.line([(x, 0), (x, height)], fill=(56, 189, 248, 18), width=1)
    for y in range(0, height, step):
        dr.line([(0, y), (width, y)], fill=(56, 189, 248, 18), width=1)

    # Circuit trace lines on left & right margins
    lw = max(2, width // 600)
    node_r = max(4, width // 260)
    traces = [
        [(0, int(height * 0.24)), (int(width * 0.14), int(height * 0.24)), (int(width * 0.18), int(height * 0.34)), (int(width * 0.28), int(height * 0.34))],
        [(0, int(height * 0.72)), (int(width * 0.11), int(height * 0.72)), (int(width * 0.16), int(height * 0.62)), (int(width * 0.24), int(height * 0.62))],
        [(width, int(height * 0.28)), (int(width * 0.86), int(height * 0.28)), (int(width * 0.81), int(height * 0.38)), (int(width * 0.72), int(height * 0.38))],
        [(width, int(height * 0.76)), (int(width * 0.88), int(height * 0.76)), (int(width * 0.82), int(height * 0.64)), (int(width * 0.75), int(height * 0.64))],
    ]
    for path in traces:
        dr.line(path, fill=(42, 224, 255, 65), width=lw)
        ex, ey = path[-1]
        dr.ellipse([ex - node_r, ey - node_r, ex + node_r, ey + node_r], fill=(42, 224, 255, 130), outline=(255, 255, 255, 180), width=1)

    return Image.alpha_composite(base, grid)


def _crop_upper_body(im: Image.Image, keep_top_ratio: float = 0.66) -> Image.Image:
    """Keep crisp upper-body / waist-up mascot framing for circular avatars and horizontal banners."""
    w, h = im.size
    return im.crop((0, 0, w, max(1, int(h * keep_top_ratio))))


def build_watermark_badge(out_path: Path = WATERMARK_PATH) -> Path:
    """Composite circular dark-navy watermark badge with current mascots & @handle from brand.yaml."""
    clear_brand_cache()
    handle = get_brand_handle()
    size = 512
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    # Outer glowing cyan & gold circle
    d.ellipse([10, 10, size - 10, size - 10], fill=(18, 24, 44, 255), outline=CYAN, width=14)
    d.ellipse([24, 24, size - 24, size - 24], outline=GOLD, width=5)

    # Circular mask for mascots inside
    inner_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    m_path = MASCOTS_DIR / "male_curious.png"
    f_path = MASCOTS_DIR / "female_explain.png"
    if m_path.exists():
        m_im = Image.open(m_path).convert("RGBA")
        m_im.thumbnail((230, 360), Image.Resampling.LANCZOS)
        inner_layer.alpha_composite(m_im, (42, 76))
    if f_path.exists():
        f_im = Image.open(f_path).convert("RGBA")
        f_im.thumbnail((230, 360), Image.Resampling.LANCZOS)
        inner_layer.alpha_composite(f_im, (size - f_im.width - 42, 76))

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([28, 28, size - 28, size - 28], fill=255)
    masked_mascots = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    masked_mascots.paste(inner_layer, (0, 0), mask)
    canvas = Image.alpha_composite(canvas, masked_mascots)

    # Bottom handle pill
    d = ImageDraw.Draw(canvas)
    pill_box = [42, 366, size - 42, 456]
    d.rounded_rectangle(pill_box, radius=44, fill=(14, 20, 36, 250), outline=GOLD, width=6)
    font_sz = 46 if len(handle) <= 13 else (38 if len(handle) <= 17 else 32)
    fnt = _pick_bold_font(font_sz)
    d.text((size // 2, 411), handle, font=fnt, fill=WHITE, anchor="mm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def build_youtube_branding_watermark(out_path: Path = YT_WATERMARK_PATH) -> Path:
    """Build minimalist 512x512 YouTube Studio player branding watermark (Settings -> Customization -> Branding).

    Designed for high contrast and readability when scaled down in YouTube's bottom-right player corner.
    """
    clear_brand_cache()
    name = get_brand_name()
    size = 512
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    # High-contrast rounded cyber badge
    d.rounded_rectangle([18, 18, size - 18, size - 18], radius=96, fill=(10, 18, 38, 240), outline=CYAN, width=14)
    d.rounded_rectangle([36, 36, size - 36, size - 36], radius=82, outline=GOLD, width=4)

    # Hero upper bust of male mascot OR microchip badge
    m_path = MASCOTS_DIR / "male_explain.png"
    inner = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if m_path.exists():
        bust = _crop_upper_body(Image.open(m_path).convert("RGBA"), keep_top_ratio=0.58)
        bust.thumbnail((340, 340), Image.Resampling.LANCZOS)
        inner.alpha_composite(bust, ((size - bust.width) // 2 + 12, 44))
    else:
        _draw_microchip_emblem(d, size // 2, 195, 160, CYAN)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, size - 40, size - 40], radius=78, fill=255)
    masked = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    masked.paste(inner, (0, 0), mask)
    canvas = Image.alpha_composite(canvas, masked)

    # Bold high-contrast bottom Subscribe / Brand ribbon
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([32, 344, size - 32, 466], radius=34, fill=(220, 38, 38, 250), outline=WHITE, width=6)
    f_sub = _pick_bold_font(54)
    d.text((size // 2, 390), "SUBSCRIBE", font=f_sub, fill=WHITE, anchor="mm")
    f_nm = _pick_mono_bold_font(26 if len(name) <= 14 else 22)
    d.text((size // 2, 438), name.upper(), font=f_nm, fill=GOLD, anchor="mm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def build_profile_picture(out_path: Path = PROFILE_PIC_PATH) -> Path:
    """Build 800x800 YouTube Channel Profile Picture tailored for YouTube's circular crop."""
    clear_brand_cache()
    name = get_brand_name()
    tagline = get_brand_tagline()
    size = 800

    bg = _draw_circuit_backdrop(size, size)

    # Concentric cyber ring inside circular safe margin
    ring_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring_layer)
    rd.ellipse([26, 26, size - 26, size - 26], outline=(42, 224, 255, 210), width=16)
    rd.ellipse([48, 48, size - 48, size - 48], outline=(255, 208, 48, 175), width=5)

    # Subtle glowing microchip watermark at top center behind heads
    _draw_microchip_emblem(rd, size // 2, 255, 190, (42, 224, 255, 55))
    bg = Image.alpha_composite(bg, ring_layer)

    # Duo mascots waist-up framed prominently inside circle (zero hand overlap)
    m_path = MASCOTS_DIR / "male_curious.png"
    f_path = MASCOTS_DIR / "female_explain.png"
    hero_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if m_path.exists():
        m_bust = _crop_upper_body(Image.open(m_path).convert("RGBA"), keep_top_ratio=0.58)
        m_bust.thumbnail((360, 460), Image.Resampling.LANCZOS)
        hero_layer.alpha_composite(m_bust, (72, 138))
    if f_path.exists():
        f_bust = _crop_upper_body(Image.open(f_path).convert("RGBA"), keep_top_ratio=0.58)
        f_bust.thumbnail((380, 460), Image.Resampling.LANCZOS)
        hero_layer.alpha_composite(f_bust, (size - f_bust.width - 54, 152))

    # Clip duo cleanly to inner circle so nothing leaks outside border
    circle_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle_mask).ellipse([36, 36, size - 36, size - 36], fill=255)
    clipped_heroes = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    clipped_heroes.paste(hero_layer, (0, 0), circle_mask)
    canvas = Image.alpha_composite(bg, clipped_heroes)

    # Crisp bottom dark glass pill well inside safe circular chord
    d = ImageDraw.Draw(canvas)
    pill_box = [112, 550, size - 112, 686]
    # Drop shadow
    d.rounded_rectangle([pill_box[0], pill_box[1] + 6, pill_box[2], pill_box[3] + 6], radius=46, fill=(0, 0, 0, 170))
    d.rounded_rectangle(pill_box, radius=46, fill=(10, 18, 38, 246), outline=GOLD, width=6)

    name_sz = 60 if len(name) <= 13 else (52 if len(name) <= 16 else 44)
    f_name = _pick_bold_font(name_sz)
    d.text((size // 2, 600), name, font=f_name, fill=WHITE, anchor="mm")

    f_tag = _pick_mono_bold_font(22)
    d.text((size // 2, 654), tagline.upper(), font=f_tag, fill=CYAN, anchor="mm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def build_channel_banner(out_path: Path = BANNER_PATH) -> Path:
    """Build 2560x1440 YouTube Channel Banner keeping all text & duo heroes inside the 1546x423 safe zone."""
    clear_brand_cache()
    name = get_brand_name()
    handle = get_brand_handle()
    W, H = 2560, 1440
    # Official YouTube safe zone (mobile & all devices): 1546x423 centered
    SAFE_W, SAFE_H = 1546, 423
    SX0 = (W - SAFE_W) // 2  # 507
    SY0 = (H - SAFE_H) // 2  # 508
    SX1 = SX0 + SAFE_W       # 2053
    SY1 = SY0 + SAFE_H       # 931

    canvas = _draw_circuit_backdrop(W, H)

    # Subtle outer TV-region decorative motifs & desktop-strip horizon lines
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    # Darken top & bottom TV bands slightly so center desktop strip pops cleanly
    dd.rectangle([0, 0, W, SY0], fill=(4, 8, 20, 110))
    dd.rectangle([0, SY1, W, H], fill=(4, 8, 20, 110))
    # Subtle glowing horizontal rails bordering the 423px desktop strip
    dd.line([(0, SY0), (W, SY0)], fill=(42, 224, 255, 110), width=3)
    dd.line([(0, SY1), (W, SY1)], fill=(42, 224, 255, 110), width=3)

    # Decorative microchip emblems in desktop side wings (visible on desktop/TV)
    _draw_microchip_emblem(dd, 255, H // 2, 136, (42, 224, 255, 95))
    _draw_microchip_emblem(dd, W - 255, H // 2, 136, (42, 224, 255, 95))
    # Subtle circular blueprint rings in TV top/bottom
    for cx, cy, rad in ((620, 260, 130), (1940, 260, 130), (620, 1180, 130), (1940, 1180, 130)):
        dd.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(42, 224, 255, 42), width=3)
        dd.ellipse([cx - rad // 2, cy - rad // 2, cx + rad // 2, cy + rad // 2], outline=(255, 208, 48, 36), width=2)
    canvas = Image.alpha_composite(canvas, deco)

    # Duo mascots positioned cleanly inside left & right of 1546x423 safe zone with clear center margin
    m_path = MASCOTS_DIR / "male_explain.png"
    f_path = MASCOTS_DIR / "female_explain.png"
    mascots_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if m_path.exists():
        m_bust = _crop_upper_body(Image.open(m_path).convert("RGBA"), keep_top_ratio=0.58)
        m_bust.thumbnail((310, 395), Image.Resampling.LANCZOS)
        mascots_layer.alpha_composite(m_bust, (SX0 + 10, SY1 - m_bust.height))
    if f_path.exists():
        f_bust = _crop_upper_body(Image.open(f_path).convert("RGBA"), keep_top_ratio=0.58)
        f_bust.thumbnail((310, 395), Image.Resampling.LANCZOS)
        mascots_layer.alpha_composite(f_bust, (SX1 - f_bust.width - 10, SY1 - f_bust.height))

    # Clip mascots strictly inside desktop strip vertically so they sit flush on SY1
    strip_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(strip_mask).rectangle([SX0, SY0 - 10, SX1, SY1], fill=255)
    clipped_mascots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    clipped_mascots.paste(mascots_layer, (0, 0), strip_mask)
    canvas = Image.alpha_composite(canvas, clipped_mascots)

    # Center Typography Block inside safe zone (generous margin between SX0+300 and SX1-300)
    d = ImageDraw.Draw(canvas)
    cx = W // 2

    # 1. Eyebrow Pill
    f_eye = _pick_mono_bold_font(21)
    eye_text = "EVERYDAY SCIENCE  •  APPLIED TECHNOLOGY"
    eb = d.textbbox((0, 0), eye_text, font=f_eye)
    ew = eb[2] - eb[0] + 44
    d.rounded_rectangle([cx - ew // 2, SY0 + 26, cx + ew // 2, SY0 + 68], radius=21, fill=(14, 28, 58, 235), outline=CYAN, width=3)
    d.text((cx, SY0 + 47), eye_text, font=f_eye, fill=CYAN, anchor="mm")

    # 2. Hero Channel Name with crisp shadow + accent underline below underscore
    title_sz = 102 if len(name) <= 13 else (90 if len(name) <= 16 else 76)
    f_title = _pick_bold_font(title_sz)
    ty = SY0 + 150
    d.text((cx + 4, ty + 5), name, font=f_title, fill=(4, 8, 18, 220), anchor="mm")
    d.text((cx, ty), name, font=f_title, fill=WHITE, anchor="mm")
    tb = d.textbbox((0, 0), name, font=f_title)
    bar_w = min(640, (tb[2] - tb[0]) + 36)
    d.rounded_rectangle([cx - bar_w // 2, SY0 + 228, cx + bar_w // 2, SY0 + 235], radius=4, fill=GOLD)

    # 3. Handle Badge + Tagline Row (compact width so zero hand overlap)
    f_handle = _pick_bold_font(27)
    f_sub = _pick_bold_font(24)
    sub_text = "HOW YOUR WORLD WORKS"
    hb = d.textbbox((0, 0), handle, font=f_handle)
    sb = d.textbbox((0, 0), sub_text, font=f_sub)
    pill_w = (hb[2] - hb[0]) + 34
    gap = 18
    total_row_w = pill_w + gap + (sb[2] - sb[0])
    rx0 = cx - total_row_w // 2
    ry = SY0 + 282
    d.rounded_rectangle([rx0, ry - 22, rx0 + pill_w, ry + 22], radius=22, fill=GOLD)
    d.text((rx0 + pill_w // 2, ry), handle, font=f_handle, fill=INK, anchor="mm")
    d.text((rx0 + pill_w + gap, ry), sub_text, font=f_sub, fill=(226, 238, 255, 255), anchor="lm")

    # 4. Schedule Pill along bottom of safe zone
    f_sched = _pick_mono_bold_font(19)
    sched_text = "2 NEW SHORTS DAILY  |  WEEKDAYS: EVERYDAY TECH  |  WEEKENDS: TECH RADAR"
    scb = d.textbbox((0, 0), sched_text, font=f_sched)
    sw = scb[2] - scb[0] + 40
    d.rounded_rectangle([cx - sw // 2, SY0 + 338, cx + sw // 2, SY0 + 382], radius=22, fill=(10, 18, 38, 230), outline=(56, 189, 248, 130), width=2)
    d.text((cx, SY0 + 360), sched_text, font=f_sched, fill=(186, 230, 253, 255), anchor="mm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def build_all_branding_assets() -> dict[str, Path]:
    """Generate the full YouTube & Shorts visual branding kit from config/brand.yaml."""
    return {
        "shorts_watermark": build_watermark_badge(),
        "youtube_watermark": build_youtube_branding_watermark(),
        "profile_picture": build_profile_picture(),
        "channel_banner": build_channel_banner(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tech-geek mascots & dynamic channel branding kit.")
    parser.add_argument("--watermark-only", action="store_true", help="Only rebuild watermark.png from current mascots & brand.yaml")
    parser.add_argument("--branding-kit", action="store_true", help="Rebuild watermark, YouTube player watermark, profile picture, and channel banner")
    parser.add_argument("--force-mascots", action="store_true", help="Overwrite existing mascot PNGs")
    args = parser.parse_args()

    if not args.watermark_only and not args.branding_kit:
        created = generate_placeholder_mascots(force=args.force_mascots)
        print(f"[brand_assets] Generated {len(created)} mascot sprites in {MASCOTS_DIR}")

    if args.branding_kit:
        kit = build_all_branding_assets()
        for key, path in kit.items():
            print(f"[brand_assets] Built {key}: {path}")
    else:
        wm = build_watermark_badge()
        print(f"[brand_assets] Built watermark badge at {wm} for {get_brand_handle()}")


if __name__ == "__main__":
    main()
