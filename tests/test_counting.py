import pytest

from blackjack_bot.counting import Shoe, Tracker
from blackjack_bot.strategy import Engine, Rules, advise


def test_fresh_shoe():
    shoe = Shoe(6)
    assert shoe.cards_left == 312 and shoe.running_count == 0
    assert shoe.probs() == pytest.approx([1 / 13] * 9 + [4 / 13])


def test_hi_lo_running_and_true_count():
    shoe = Shoe(2)
    for value in (2, 3, 6, 10, 1, 7, 5):
        shoe.remove(value)
    assert shoe.running_count == 2
    assert shoe.true_count == pytest.approx(2 / (97 / 52))


def test_house_edge_is_realistic():
    assert Engine().game_ev() == pytest.approx(-0.005, abs=0.002)
    six_to_five = Engine(rules=Rules(blackjack_payout=1.2)).game_ev()
    assert six_to_five == pytest.approx(Engine().game_ev() - 0.3 * 2 * (1 / 13) * (4 / 13) * (1 - 0.047),
                                        abs=0.001)


def test_edge_rises_with_the_count():
    rich, poor = Shoe(6), Shoe(6)
    for _ in range(30):
        rich.remove(5)
        poor.remove(10)
    assert Engine(rich.probs()).game_ev() > 0 > Engine().game_ev() > Engine(poor.probs()).game_ev()


def test_count_changes_the_play():
    rich = Shoe(6)
    for value in [2, 3, 4, 5, 6] * 10:
        rich.remove(value)
    no_surrender = Rules(surrender=False)
    assert advise(["10", "6"], "10", no_surrender).action == "hit"
    assert advise(["10", "6"], "10", no_surrender, rich.probs()).action == "stand"
    assert advise(["A", "9"], "A").insurance_ev < 0
    assert advise(["A", "9"], "A", probs=rich.probs()).insurance_ev > 0


def test_tracker_counts_each_card_once_per_round():
    t = Tracker(6)
    t.observe({"dealer": ["6"], "player": ["10", "5"], "other0": ["2", "3"]})
    t.observe({"dealer": ["6"], "player": ["10", "5"], "other0": ["2", "3", "4"]})
    t.observe({"dealer": ["6", "10", "9"], "player": ["10", "5"], "other0": ["2", "3", "4"]})
    assert t.shoe.cards_seen == 0  # round still in progress
    live = t.current_shoe()
    assert live.cards_seen == 8 and live.running_count == 3
    t.observe({"dealer": [], "player": [], "other0": []})  # table cleared
    assert t.shoe.cards_seen == 8 and t.shoe.running_count == 3 and t.rounds_counted == 1
    t.observe({"dealer": ["K"], "player": ["A", "?"], "other0": []})  # '?' is not counted
    assert t.current_shoe().running_count == 1


def test_tracker_handles_back_to_back_rounds_and_reset():
    t = Tracker(1)
    t.observe({"dealer": ["6", "10"], "player": ["10", "5", "9"]})
    t.observe({"dealer": ["3"], "player": ["2", "2"]})  # fewer cards: a new round began
    assert t.shoe.cards_seen == 5
    assert t.current_shoe().cards_seen == 8
    t.reset()
    assert t.current_shoe().cards_seen == 0


def test_tracker_flags_a_missed_shuffle():
    t = Tracker(1)
    for _ in range(3):
        t.observe({"player": ["A", "A"]})
        t.observe({"player": []})
    assert t.overdrawn
