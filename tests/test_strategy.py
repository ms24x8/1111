import pytest

from blackjack_bot.strategy import Engine, Rules, advise

S17 = Rules()
NO_SURRENDER = Rules(surrender=False)


def best(cards, dealer, rules=NO_SURRENDER):
    return advise(cards.split(), dealer, rules).action


def test_dealer_distribution_sums_to_one():
    for up in range(1, 11):
        for h17 in (False, True):
            engine = Engine(rules=Rules(hit_soft_17=h17))
            assert sum(engine.dealer_distribution(up)) == pytest.approx(1.0)


def test_dealer_bust_odds_match_published_figures():
    # Infinite deck, S17, no dealer blackjack.
    assert Engine().dealer_distribution(6)[-1] == pytest.approx(0.423, abs=0.002)
    assert Engine().dealer_distribution(10)[-1] == pytest.approx(0.230, abs=0.002)


@pytest.mark.parametrize("cards,dealer,expected", [
    # hard totals
    ("10 6", "10", "hit"), ("10 6", "6", "stand"), ("10 2", "2", "hit"),
    ("10 2", "3", "hit"), ("10 2", "4", "stand"), ("10 3", "2", "stand"),
    ("10 7", "A", "stand"), ("5 3", "6", "hit"),
    # doubles
    ("6 5", "6", "double"), ("6 5", "10", "double"), ("6 4", "9", "double"),
    ("6 4", "10", "hit"), ("5 4", "3", "double"), ("5 4", "2", "hit"),
    # soft totals
    ("A 7", "9", "hit"), ("A 7", "7", "stand"), ("A 7", "5", "double"),
    ("A 6", "3", "double"), ("A 6", "2", "hit"), ("A 8", "6", "stand"),
    ("A 2", "6", "double"), ("A 2", "4", "hit"),
    # pairs
    ("A A", "10", "split"), ("8 8", "10", "split"), ("8 8", "A", "split"),
    ("10 10", "6", "stand"), ("5 5", "6", "double"), ("9 9", "7", "stand"),
    ("9 9", "9", "split"), ("4 4", "5", "split"), ("4 4", "4", "hit"),
    ("2 2", "7", "split"), ("2 2", "8", "hit"), ("6 6", "2", "split"),
    ("7 7", "8", "hit"),
    # multi-card hands: no double or split any more
    ("5 3 3", "6", "hit"), ("4 4 4", "5", "stand"),
    ("A 3 4", "9", "hit"),
])
def test_basic_strategy(cards, dealer, expected):
    assert best(cards, dealer) == expected


def test_surrender():
    assert best("10 6", "10", S17) == "surrender"
    assert best("10 6", "9", S17) == "surrender"
    assert best("10 5", "10", S17) == "surrender"
    assert best("10 5", "9", S17) == "hit"
    assert best("8 8", "10", S17) == "split"
    assert best("10 7", "A", Rules(hit_soft_17=True)) == "surrender"


def test_h17_changes_doubling():
    assert best("6 5", "A", Rules(surrender=False)) == "hit"
    assert best("6 5", "A", Rules(surrender=False, hit_soft_17=True)) == "double"


def test_known_ev_values():
    a = advise(["6", "5"], "6", S17)
    assert a.evs["double"] == pytest.approx(0.667, abs=0.005)
    a = advise(["10", "6"], "10", S17)
    assert a.evs["stand"] == pytest.approx(-0.540, abs=0.003)
    assert a.evs["hit"] == pytest.approx(-0.540, abs=0.005)
    assert a.win + a.push + a.lose == pytest.approx(1.0)


def test_odds_consistent_with_ev():
    for cards in (["9", "7"], ["A", "6"], ["10", "10"], ["3", "2", "4"]):
        for dealer in ("2", "6", "9", "A"):
            a = advise(cards, dealer, NO_SURRENDER)
            assert a.win + a.push + a.lose == pytest.approx(1.0)
            if a.action in ("hit", "stand"):
                assert a.win - a.lose == pytest.approx(a.ev)


def test_blackjack_and_bust():
    assert advise(["A", "K"], "5").action == "blackjack"
    assert advise(["A", "K"], "5", Rules(blackjack_payout=1.2)).ev == 1.2
    assert advise(["10", "9", "5"], "5").action == "bust"
    assert advise(["7", "7", "7"], "5").action == "stand"  # 21 in three cards


def test_bad_input():
    with pytest.raises(ValueError):
        advise(["X", "5"], "5")
    with pytest.raises(ValueError):
        advise(["5"], "5")
