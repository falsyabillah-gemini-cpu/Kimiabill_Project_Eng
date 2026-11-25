"""Extract clean transparent RGBA PNG sprites from white-background mascot sheets.

Uses border-connected flood-fill (BFS) so pure white areas INSIDE the characters
(like white lab coats, eyes, and shoes) remain 100% opaque while exterior white
background becomes cleanly transparent with smooth anti-aliased edges.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def remove_exterior_white(im: Image.Image, thresh: int = 240) -> Image.Image:
    rgba = im.convert("RGBA")
    arr = np.array(rgba, dtype=np.uint8)
    h, w, _ = arr.shape
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # Candidate background pixels: very bright & near-neutral white
    is_white = (r >= thresh) & (g >= thresh) & (b >= thresh) & (
        (np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)) <= 22
    )

    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()

    # Seed from all 4 borders
    for x in range(w):
        if is_white[0, x] and not visited[0, x]:
            visited[0, x] = True
            q.append((0, x))
        if is_white[h - 1, x] and not visited[h - 1, x]:
            visited[h - 1, x] = True
            q.append((h - 1, x))
    for y in range(h):
        if is_white[y, 0] and not visited[y, 0]:
            visited[y, 0] = True
            q.append((y, 0))
        if is_white[y, w - 1] and not visited[y, w - 1]:
            visited[y, w - 1] = True
            q.append((y, w - 1))

    # 4-connected BFS flood fill
    while q:
        cy, cx = q.popleft()
        for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_white[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))

    # Also soften 1px white halo directly bordering exterior background
    alpha = np.where(visited, 0, 255).astype(np.uint8)
    # Feather only pixels immediately adjacent to background that are light gray/white
    dilated = np.array(
        Image.fromarray(visited.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))
    ) > 0
    halo = dilated & (~visited) & (r >= 205) & (g >= 205) & (b >= 205)
    alpha[halo] = 0

    arr[:, :, 3] = alpha
    out = Image.fromarray(arr, mode="RGBA")
    bbox = out.getbbox()
    return out.crop(bbox) if bbox else out


def split_duo_sheet(sheet_path: Path, out_male: Path, out_female: Path) -> None:
    im = Image.open(sheet_path).convert("RGBA")
    w, h = im.size
    left = remove_exterior_white(im.crop((0, 0, w // 2, h)))
    right = remove_exterior_white(im.crop((w // 2, 0, w, h)))
    out_male.parent.mkdir(parents=True, exist_ok=True)
    left.save(out_male)
    right.save(out_female)
    print(f"Saved {out_male.name} {left.size} & {out_female.name} {right.size}")


def main() -> None:
    artifact_dir = Path(
        "/home/antigravity/.gemini/jetski/brain/v1/default/jetski_remote_agents_3p_devbox/81f22d01-5916-4225-a103-1712f704dd8b"
    )
    out_dir = Path("/home/antigravity/Kimiabill_videos/assets/mascots")
    sheets = [
        ("mascot_option_b_anime_1795350075395.jpg", "male_explain.png", "female_explain.png"),
        ("mascot_b_curious_hook_1795350233557.jpg", "male_curious.png", "female_curious.png"),
        ("mascot_b_celebrate_terjawab_1795350234034.jpg", "male_celebrate.png", "female_celebrate.png"),
    ]
    for src_name, m_name, f_name in sheets:
        split_duo_sheet(artifact_dir / src_name, out_dir / m_name, out_dir / f_name)

    # Create quick dark-bg preview to verify crisp transparency & intact white lab coats
    preview = Image.new("RGBA", (1200, 620), (30, 34, 54, 255))
    names = [
        "male_curious.png", "female_curious.png",
        "male_explain.png", "female_explain.png",
        "male_celebrate.png", "female_celebrate.png",
    ]
    for i, nm in enumerate(names):
        sp = Image.open(out_dir / nm).convert("RGBA")
        sp.thumbnail((185, 280))
        x = 20 + i * 195
        y = (620 - sp.height) // 2
        preview.alpha_composite(sp, (x, y))
    preview.convert("RGB").save("/tmp/mascot_transparency_check.jpg", quality=90)


if __name__ == "__main__":
    main()
