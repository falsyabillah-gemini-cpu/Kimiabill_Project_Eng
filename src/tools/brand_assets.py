"""Generate nerdy blue-haired tech-geek mascot sprites (placeholder flat-vector)
and dynamically composite `assets/watermark.png` from `config/brand.yaml`.

Usage:
  python -m src.tools.brand_assets                  # Generate tech-geek mascots (if missing/forced) + watermark
  python -m src.tools.brand_assets --watermark-only # Rebuild only assets/watermark.png using current mascots & @handle
"""
from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from src.brand_config import BASE_DIR, clear_brand_cache, get_brand_handle

MASCOTS_DIR = BASE_DIR / "assets" / "mascots"
WATERMARK_PATH = BASE_DIR / "assets" / "watermark.png"

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
    ):
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


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
    pill_box = [48, 366, size - 48, 456]
    d.rounded_rectangle(pill_box, radius=44, fill=(14, 20, 36, 250), outline=GOLD, width=6)
    font_sz = 46 if len(handle) <= 13 else (38 if len(handle) <= 17 else 32)
    fnt = _pick_bold_font(font_sz)
    d.text((size // 2, 411), handle, font=fnt, fill=WHITE, anchor="mm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tech-geek mascots & dynamic watermark badge.")
    parser.add_argument("--watermark-only", action="store_true", help="Only rebuild watermark.png from current mascots & brand.yaml")
    parser.add_argument("--force-mascots", action="store_true", help="Overwrite existing mascot PNGs")
    args = parser.parse_args()

    if not args.watermark_only:
        created = generate_placeholder_mascots(force=args.force_mascots)
        print(f"[brand_assets] Generated {len(created)} mascot sprites in {MASCOTS_DIR}")
    wm = build_watermark_badge()
    print(f"[brand_assets] Built watermark badge at {wm} for {get_brand_handle()}")


if __name__ == "__main__":
    main()
