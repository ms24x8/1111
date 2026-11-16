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
# Besides the ranks there are helper classes: "1" and "0" (the digits of a ten
# seen separately), "10" (its digits touching) and the suit symbols, which are
# recognised so they can be skipped instead of being mistaken for a rank.
SUITS = ["\u2660", "\u2665", "\u2666", "\u2663"]
TEMPLATE_CLASSES = (["A", "2", "3", "4", "5", "6", "7", "8", "9", "J", "Q", "K", "1", "0", "10"]
                    + SUITS)
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
    """Recognise one glyph (dark ink on white).

    Returns a rank, "1" or "0" (half of a ten), "suit", or "" when unsure.
    An uncertain match is never turned into a guess: wrong cards would mean
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
    # All four suits count as one answer, so they don't compete with each other.
    first_suit = len(TEMPLATE_CLASSES) - len(SUITS)
    merged = np.append(per_class[:first_suit], per_class[first_suit:].max())
    names = TEMPLATE_CLASSES[:first_suit] + ["suit"]
    order = np.argsort(merged)[::-1]
    confident = (merged[order[0]] >= MIN_SCORE
                 and merged[order[0]] - merged[order[1]] >= MIN_MARGIN)
    result = names[order[0]] if confident else ""
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = result
    return result


def _card_blobs(bgr: np.ndarray) -> list[tuple[tuple[int, int, int, int], np.ndarray]]:
    """Return (bounding box, filled mask) for each card-face blob."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 70) & (hsv[..., 2] > 190)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    region_h = bgr.shape[0]
    blobs = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < max(24, 0.15 * region_h) or w < 0.3 * h:
            continue
        if cv2.contourArea(contour) < 0.5 * w * h:
            continue
        mask = np.zeros(white.shape, np.uint8)
        # Fill the convex hull, not the raw outline: ink that touches the card's
        # edge would otherwise open a notch and fall outside the card.
        cv2.drawContours(mask, [cv2.convexHull(contour)], -1, 255, cv2.FILLED)
        blobs.append(((x, y, w, h), mask))
    # Cards in one area are the same size; much shorter white shapes are
    # something else, such as a badge showing the hand total.
    tallest = max((b[0][3] for b in blobs), default=0)
    blobs = [b for b in blobs if b[0][3] >= 0.7 * tallest]
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
        if x <= 1 or y <= 1:
            # The card runs off the top/left of the area, so its rank may be cut
            # off. A clipped rank can look like another one: don't read it.
            ranks.append((x, "?"))
            continue
        band_h = int(0.55 * h)
        # Ignore a thin rim: there the card's own edge and shadow would join
        # glyphs to the background.
        rim = max(1, int(0.025 * h))
        inner = cv2.erode(mask, np.ones((2 * rim + 1, 2 * rim + 1), np.uint8))
        ink = cv2.bitwise_and(inner, cv2.bitwise_not(white))[y:y + band_h, x:x + w]
        face = white[y:y + band_h, x:x + w]
        # Darkest channel: black and red ink both come out dark on a white face.
        dark = np.where(mask > 0, bgr.min(axis=2), 255).astype(np.uint8)[y:y + band_h, x:x + w]
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        glyphs = []
        for gx, gy, gw, gh, area in stats[1:count]:
            if not 0.07 * h <= gh <= 0.48 * h or not 0.035 * h <= gw <= 0.6 * h or area < 0.15 * gw * gh:
                continue
            if gy + gh >= band_h:  # cut by the strip: a centre pip, not an index
                continue
            glyphs.append([int(gx), int(gy), int(gw), int(gh), 1])
        glyphs = sorted(g for g in glyphs if _is_index(g, glyphs, face, h))
        found = []
        for gx, gy, gw, gh, _ in glyphs:
            pad = 2
            crop = dark[max(0, gy - pad):gy + gh + pad, max(0, gx - pad):gx + gw + pad]
            name = classify_glyph(crop)
            if name != "suit":  # a suit printed beside the rank is not a card
                found.append((gx, gy, gw, gh, name))
        index = 0
        while index < len(found):
            gx, gy, gw, gh, name = found[index]
            if name == "1":
                # A ten: swallow its "0", which may be partly hidden by the next card.
                nxt = found[index + 1] if index + 1 < len(found) else None
                if nxt and nxt[4] in ("0", "") and nxt[0] - (gx + gw) < 0.6 * gh:
                    index += 1
                name = "10"
            elif name not in VALID_RANKS:  # unsure, or a stray "0"
                name = "?"
            ranks.append((x + gx, name))
            if debug is not None:
                debug.append((x + gx, y + gy, gw, gh, name))
            index += 1
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
    return gap <= 0.20 * card_h


def grab(sct, box: list[int]) -> np.ndarray:
    """Capture a screen region [x, y, w, h] with an mss instance, as BGR."""
    x, y, w, h = box
    shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
    return np.asarray(shot)[..., :3].copy()
