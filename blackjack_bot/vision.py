"""Read card ranks from a screenshot region.

Approach (no per-game templates needed):
  1. Find the bright, low-saturation blobs in the region - the card faces.
  2. Inside each blob, look at the top strip, where the rank index of every
     (possibly fanned / overlapping) card is printed.
  3. Keep the glyphs sitting just under a card's top edge, merge the two
     glyphs of a "10", and match every other glyph against rank templates
     rendered from many fonts (rank_templates.npz).

Works for the common look: light card faces, ranks in the top-left corner,
cards laid out side by side or fanned horizontally.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

VALID_RANKS = {"A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"}
# "1" and "0" catch the halves of a "10" whose digits were not seen together:
# a lone "1" can only be a ten; a lone "0" is rejected rather than misread.
TEMPLATE_CLASSES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "J", "Q", "K", "1", "0"]
TEMPLATE_PATH = Path(__file__).with_name("rank_templates.npz")
TARGET_CARD_HEIGHT = 300
MIN_SCORE = 0.55  # minimum similarity to the best template
MIN_MARGIN = 0.04  # ...and minimum lead over the best template of another rank

_templates: tuple[np.ndarray, np.ndarray] | None = None
_cache: dict[bytes, str] = {}


def glyph_vector(gray: np.ndarray) -> np.ndarray | None:
    """Normalise a dark-on-light glyph image into a unit feature vector."""
    ys, xs = np.where(gray < 128)
    if len(ys) == 0:
        return None
    tight = gray[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    small = cv2.resize(255 - tight, (20, 28), interpolation=cv2.INTER_AREA).astype(np.float32)
    vector = cv2.GaussianBlur(small, (3, 3), 0).ravel()
    vector -= vector.mean()
    norm = np.linalg.norm(vector)
    return vector / norm if norm else None


def _load_templates() -> tuple[np.ndarray, np.ndarray]:
    global _templates
    if _templates is None:
        data = np.load(TEMPLATE_PATH)
        vectors = data["vectors"].astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        _templates = (vectors, data["labels"])
    return _templates


def classify_glyph(gray: np.ndarray) -> str:
    """Recognise one rank glyph (dark ink on white). Returns a rank or ''.

    An uncertain match returns '' rather than a guess: wrong cards would mean
    wrong advice.
    """
    vector = glyph_vector(gray)
    if vector is None:
        return ""
    key = np.round(vector * 40).astype(np.int8).tobytes()
    if key in _cache:
        return _cache[key]
    vectors, labels = _load_templates()
    scores = vectors @ vector
    per_class = np.full(len(TEMPLATE_CLASSES), -1.0)
    np.maximum.at(per_class, labels, scores)
    order = np.argsort(per_class)[::-1]
    best = TEMPLATE_CLASSES[order[0]]
    confident = (per_class[order[0]] >= MIN_SCORE
                 and per_class[order[0]] - per_class[order[1]] >= MIN_MARGIN)
    if best == "1":
        best = "10"
    result = best if confident and best in VALID_RANKS else ""
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = result
    return result


def _card_blobs(bgr: np.ndarray) -> list[tuple[tuple[int, int, int, int], np.ndarray]]:
    """Return (bounding box, filled mask) for each card-face blob."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 70) & (hsv[..., 2] > 165)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    region_h = bgr.shape[0]
    blobs = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < max(30, 0.2 * region_h) or w < 0.3 * h:
            continue
        if cv2.contourArea(contour) < 0.5 * w * h:
            continue
        mask = np.zeros(white.shape, np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
        blobs.append(((x, y, w, h), mask))
    blobs.sort(key=lambda b: b[0][0])
    return blobs, white


def read_ranks(bgr: np.ndarray, debug: list | None = None) -> list[str]:
    """Return the card ranks visible in a BGR image, left to right.

    Unreadable glyphs come back as '?'.  If `debug` is a list, the glyph boxes
    (x, y, w, h, rank), in upscaled coordinates, are appended to it.
    """
    blobs, white = _card_blobs(bgr)
    if not blobs:
        return []
    # Small cards have tiny rank glyphs; enlarge so OCR has enough pixels.
    zoom = min(4.0, TARGET_CARD_HEIGHT / max(b[0][3] for b in blobs))
    if zoom > 1.2:
        bgr = cv2.resize(bgr, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
        blobs, white = _card_blobs(bgr)

    ranks: list[tuple[int, str]] = []
    for (x, y, w, h), mask in blobs:
        band_h = int(0.40 * h)
        ink = cv2.bitwise_and(mask, cv2.bitwise_not(white))[y:y + band_h, x:x + w]
        face = white[y:y + band_h, x:x + w]
        # Darkest channel: black and red ink both come out dark on a white face.
        dark = np.where(mask > 0, bgr.min(axis=2), 255).astype(np.uint8)[y:y + band_h, x:x + w]
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        glyphs = []
        for gx, gy, gw, gh, area in stats[1:count]:
            if not 0.07 * h <= gh <= 0.30 * h or gw > 0.25 * h or area < 0.15 * gw * gh:
                continue
            if gy + gh >= band_h:  # cut by the strip: a centre pip, not an index
                continue
            glyphs.append([int(gx), int(gy), int(gw), int(gh), 1])
        glyphs = [g for g in glyphs if _is_index(g, glyphs, face, h)]
        glyphs.sort()
        merged: list[list[int]] = []
        for g in glyphs:
            last = merged[-1] if merged else None
            if last and g[0] - (last[0] + last[2]) < 0.4 * g[3] and abs(g[1] - last[1]) < 0.3 * g[3]:
                x2 = max(last[0] + last[2], g[0] + g[2])
                y2 = max(last[1] + last[3], g[1] + g[3])
                last[0], last[1] = min(last[0], g[0]), min(last[1], g[1])
                last[2], last[3], last[4] = x2 - last[0], y2 - last[1], last[4] + 1
            else:
                merged.append(list(g))
        for gx, gy, gw, gh, parts in merged:
            if parts >= 2:
                rank = "10"  # the only two-glyph rank
            else:
                pad = 2
                crop = dark[max(0, gy - pad):gy + gh + pad, max(0, gx - pad):gx + gw + pad]
                rank = classify_glyph(crop) or "?"
            ranks.append((x + gx, rank))
            if debug is not None:
                debug.append((x + gx, y + gy, gw, gh, rank))
    return [rank for _, rank in sorted(ranks)]


def _is_index(glyph, glyphs, face: np.ndarray, card_h: int) -> bool:
    """True if a glyph is a rank index: the first thing below a card's top edge."""
    gx, gy, gw, gh, _ = glyph
    for ox, oy, ow, oh, _ in glyphs:  # something printed above it -> suit symbol
        if oy + oh <= gy and ox < gx + gw and gx < ox + ow:
            return False
    # Walk up from the glyph through blank card face to the card edge.
    column = face[:gy, gx + gw // 2]
    gap = 0
    while gap < len(column) and column[len(column) - 1 - gap]:
        gap += 1
    return gap <= 0.11 * card_h


def grab(sct, box: list[int]) -> np.ndarray:
    """Capture a screen region [x, y, w, h] with an mss instance, as BGR."""
    x, y, w, h = box
    shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
    return np.asarray(shot)[..., :3].copy()
