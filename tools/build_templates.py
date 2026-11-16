"""Rebuild blackjack_bot/rank_templates.npz from the fonts installed on this machine.

Each rank character is rendered in every usable font and stored as a small
normalised image; the bot recognises on-screen ranks by nearest match.

    python tools/build_templates.py [extra-font.ttf ...]
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blackjack_bot.vision import TEMPLATE_CLASSES, TEMPLATE_PATH, glyph_vector  # noqa: E402

SKIP = ("italic", "oblique", "mono", "thin", "light", "unifont", "emoji", "symbol", "math",
        "awesome", "tlwg", "noto")


def system_fonts() -> list[str]:
    out = subprocess.run(["fc-list", ":", "file"], capture_output=True, text=True).stdout
    paths = sorted({line.split(":")[0] for line in out.splitlines()})
    return [p for p in paths if not any(word in p.lower() for word in SKIP)]


def render(font_path: str, char: str, size: int = 80):
    font = ImageFont.truetype(font_path, size)
    image = Image.new("L", (size * 2, size * 2), 255)
    ImageDraw.Draw(image).text((size // 2, size // 3), char, font=font, fill=0)
    return glyph_vector(np.asarray(image))


def main() -> None:
    vectors, labels, used = [], [], 0
    for path in system_fonts() + sys.argv[1:]:
        try:
            missing = render(path, "\uffff")  # what this font draws for a glyph it lacks
            glyphs = [render(path, c) for c in TEMPLATE_CLASSES]
        except Exception:
            continue
        if glyphs[0] is None or glyphs[11] is None or float(glyphs[0] @ glyphs[11]) > 0.9:
            continue  # no Latin letters in this font
        used += 1
        for index, glyph in enumerate(glyphs):
            if glyph is None or (missing is not None and float(glyph @ missing) > 0.95):
                continue
            vectors.append(np.round(glyph * 127 / np.abs(glyph).max()).astype(np.int8))
            labels.append(index)
    np.savez_compressed(TEMPLATE_PATH, vectors=np.array(vectors), labels=np.array(labels, np.uint8))
    counts = np.bincount(labels, minlength=len(TEMPLATE_CLASSES))
    print(f"{used} fonts -> {len(vectors)} templates -> {TEMPLATE_PATH}")
    print("per class:", dict(zip(TEMPLATE_CLASSES, counts.tolist())))


if __name__ == "__main__":
    main()
