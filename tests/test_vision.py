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
    from blackjack_bot.vision import classify_glyph

    blob = np.full((40, 30), 255, np.uint8)
    blob[5:35, 5:25] = 0  # a solid block is not a rank
    assert classify_glyph(blob) == ""
