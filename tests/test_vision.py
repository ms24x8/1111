"""Recognition tests on synthetic table screenshots drawn with PIL."""
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from blackjack_bot.vision import read_ranks

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SUITS = "♠♥♦♣"


def draw_hand(ranks, card_h=180, fan=0.32, font_path=FONT, face_down=0, drop=0):
    """Render cards on green felt; fan = horizontal offset as a fraction of card width."""
    card_w = int(card_h * 0.7)
    step = int(card_w * fan)
    n = len(ranks) + face_down
    img = Image.new("RGB", (60 + step * (n - 1) + card_w, card_h + 60 + drop * n), (20, 110, 60))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, int(card_h * 0.16))
    small = ImageFont.truetype(FONT, int(card_h * 0.13))
    big = ImageFont.truetype(FONT, int(card_h * 0.4))
    for i in range(n):
        x, y = 30 + i * step, 30 + i * drop
        box = (x, y, x + card_w, y + card_h)
        if i >= len(ranks):  # face-down card: patterned back
            d.rounded_rectangle(box, radius=card_h // 14, fill=(150, 30, 40), outline=(90, 10, 20))
            continue
        rank, suit = ranks[i], SUITS[i % 4]
        ink = (200, 20, 30) if suit in "♥♦" else (15, 15, 15)
        d.rounded_rectangle(box, radius=card_h // 14, fill=(250, 250, 248), outline=(120, 120, 120))
        pad = int(card_h * 0.05)
        d.text((x + pad, y + pad * 0.6), rank, font=font, fill=ink)
        d.text((x + pad, y + pad + card_h * 0.17), suit, font=small, fill=ink)
        d.text((x + card_w * 0.5, y + card_h * 0.55), suit, font=big, fill=ink, anchor="mm")
        d.text((x + card_w - pad, y + card_h - pad), rank, font=font, fill=ink, anchor="rd")
    return np.asarray(img)[..., ::-1].copy()


ALL = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


@pytest.mark.parametrize("card_h", [70, 90, 140, 220])
@pytest.mark.parametrize("font_path", [FONT, SERIF])
def test_every_rank_side_by_side(card_h, font_path):
    for chunk in (ALL[:7], ALL[7:]):
        assert read_ranks(draw_hand(chunk, card_h, fan=1.1, font_path=font_path)) == chunk


@pytest.mark.parametrize("hand", [["A", "7"], ["10", "6", "5"], ["8", "8"], ["K", "10", "Q"],
                                  ["2", "3", "4", "5", "6"], ["9", "6"], ["10", "10"]])
def test_fanned_hands(hand):
    assert read_ranks(draw_hand(hand, fan=0.4)) == hand
    assert read_ranks(draw_hand(hand, fan=0.4, drop=12)) == hand


def test_ten_with_partly_covered_zero():
    assert read_ranks(draw_hand(["10", "6", "5"], fan=0.32)) == ["10", "6", "5"]


def test_dealer_with_face_down_card():
    assert read_ranks(draw_hand(["6"], fan=1.1, face_down=1)) == ["6"]
    assert read_ranks(draw_hand(["10"], fan=0.5, face_down=1)) == ["10"]


def test_empty_table():
    assert read_ranks(np.full((200, 400, 3), (60, 110, 20), np.uint8)) == []


def test_uncertain_glyph_is_not_guessed():
    from blackjack_bot.vision import VALID_RANKS, classify_glyph

    blob = np.full((40, 30), 255, np.uint8)
    blob[5:35, 5:25] = 0  # a solid block is not a rank
    assert classify_glyph(blob) not in VALID_RANKS


def test_card_cut_off_by_the_area_is_not_read():
    image = draw_hand(["A", "7"], fan=0.4)
    assert read_ranks(image[38:]) == ["?"]  # top of the cards is outside the area


# ---- live-dealer style: small on-screen cards with rank and suit side by side ----

def draw_mini_hand(ranks, card_h=64, face_down=0, badge=None):
    """Mini cards: rank top-left, suit top-right, both repeated upside down below."""
    card_w = int(card_h * 0.72)
    sliver = int(card_w * 0.45)  # all but the last card show only their left part
    n = len(ranks)
    width = 40 + sliver * (n - 1) + card_w * (1 + face_down) + 6 * face_down
    img = Image.new("RGB", (width + 40, card_h + 90), (22, 30, 48))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, int(card_h * 0.42))
    for i, rank in enumerate(ranks):
        x, y = 20 + i * sliver, 20
        suit = SUITS[(i + 1) % 4]
        ink = (205, 30, 40) if suit in "♥♦" else (10, 10, 10)
        d.rounded_rectangle((x, y, x + card_w, y + card_h), radius=4, fill=(245, 245, 245),
                            outline=(200, 200, 200))
        # A "10" is drawn smaller so it fits the same space as one character.
        rank_font = font if rank != "10" else ImageFont.truetype(FONT, int(card_h * 0.29))
        d.text((x + card_w * 0.25, y + card_h * 0.27), rank, font=rank_font, fill=ink, anchor="mm")
        d.text((x + card_w * 0.76, y + card_h * 0.27), suit, font=font, fill=ink, anchor="mm")
        d.text((x + card_w * 0.27, y + card_h * 0.73), suit, font=font, fill=ink, anchor="mm")
        corner = Image.new("RGBA", (card_w // 2, card_h // 2), (0, 0, 0, 0))
        ImageDraw.Draw(corner).text((corner.width / 2, corner.height / 2), rank, font=rank_font,
                                    fill=ink, anchor="mm")
        corner = corner.rotate(180)
        img.paste(corner, (x + card_w // 2, y + card_h // 2), corner)
    for j in range(face_down):
        x = 20 + (n - 1) * sliver + (card_w + 6) * (j + 1)
        d.rounded_rectangle((x, 20, x + card_w, 20 + card_h), radius=4, fill=(168, 168, 168))
    if badge:  # the white pill showing the hand total
        d.rounded_rectangle((30, card_h + 40, 90, card_h + 70), radius=15, fill=(255, 255, 255))
        d.text((60, card_h + 55), badge, font=ImageFont.truetype(FONT, 20), fill=(20, 20, 20),
               anchor="mm")
    return np.asarray(img)[..., ::-1].copy()


@pytest.mark.parametrize("card_h", [48, 64, 90])
@pytest.mark.parametrize("hand", [["Q", "6"], ["9"], ["A", "7", "3"], ["10", "5"], ["K", "10"],
                                  ["2", "4", "8", "J"], ["5", "5"]])
def test_mini_cards(hand, card_h):
    assert read_ranks(draw_mini_hand(hand, card_h)) == hand


def test_mini_dealer_with_hole_card_and_total_badge():
    assert read_ranks(draw_mini_hand(["9"], face_down=1, badge="9")) == ["9"]
    assert read_ranks(draw_mini_hand(["10"], face_down=1, badge="10")) == ["10"]


def test_real_live_dealer_screenshot():
    """Crops from a real (downscaled) live-dealer table: dealer 9, player Q 6."""
    import cv2
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures"
    assert read_ranks(cv2.imread(str(fixtures / "live_dealer_9.png"))) == ["9"]
    assert read_ranks(cv2.imread(str(fixtures / "live_player_Q6.png"))) == ["Q", "6"]
