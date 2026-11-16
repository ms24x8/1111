"""Exact blackjack expected-value engine.

Computes the EV of every legal action (stand / hit / double / split /
surrender) for a player hand against a dealer upcard, then recommends the
highest-EV one.  Nothing is looked up in a chart: every number is derived from
the probability of each card being drawn next.

Those draw probabilities default to a full deck, but can be the composition of
the cards actually left in the shoe (see counting.py), which is what makes the
advice count-aware.  Draws are treated as independent at the given
probabilities, which is accurate to a fraction of a percent for shoe games.
The dealer is assumed to have already peeked for blackjack (standard US rules).

EVs are in units of the original bet: +1.0 means "win one bet on average".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

# Draw odds for card values 1..10 (1 = ace, 10 = any ten-value card), full deck.
FULL_DECK = tuple([1 / 13] * 9 + [4 / 13])

RANK_VALUE = {"A": 1, "J": 10, "Q": 10, "K": 10, "T": 10}
RANK_VALUE.update({str(n): n for n in range(2, 11)})

DEALER_OUTCOMES = (17, 18, 19, 20, 21, 22)  # 22 stands for "bust"

ACTION_NAMES = {
    "stand": "STAND",
    "hit": "HIT",
    "double": "DOUBLE",
    "split": "SPLIT",
    "surrender": "SURRENDER",
}


@dataclass(frozen=True)
class Rules:
    hit_soft_17: bool = False  # dealer hits soft 17 (H17) instead of standing
    double_after_split: bool = True
    surrender: bool = True  # late surrender allowed
    blackjack_payout: float = 1.5  # 3:2 = 1.5, 6:5 = 1.2


@dataclass
class Advice:
    action: str  # key of ACTION_NAMES, or "blackjack" / "bust"
    evs: dict[str, float]  # EV of every legal action
    total: int
    soft: bool
    win: float  # outcome odds when following the advice
    push: float
    lose: float
    notes: list[str] = field(default_factory=list)
    insurance_ev: float | None = None  # per unit insured, when the dealer shows an ace

    @property
    def ev(self) -> float:
        return self.evs.get(self.action, 0.0)

    @property
    def label(self) -> str:
        return ACTION_NAMES.get(self.action, self.action.upper())


def card_value(rank: str) -> int:
    try:
        return RANK_VALUE[rank.strip().upper()]
    except KeyError:
        raise ValueError(f"unknown card rank: {rank!r}") from None


def add_card(total: int, soft: bool, card: int) -> tuple[int, bool]:
    """Add a card to a hand given as (best total, has an ace counted as 11)."""
    hard = (total - 10 if soft else total) + card
    if (soft or card == 1) and hard + 10 <= 21:
        return hard + 10, True
    return hard, False


def hand_total(values: list[int]) -> tuple[int, bool]:
    total, soft = 0, False
    for v in values:
        total, soft = add_card(total, soft, v)
    return total, soft


WPL = tuple[float, float, float]  # (win, push, lose) odds; EV = win - lose


def _ev(wpl: WPL) -> float:
    return wpl[0] - wpl[2]


class Engine:
    """EV calculator for one set of draw probabilities and table rules."""

    def __init__(self, probs: tuple[float, ...] = FULL_DECK, rules: Rules = Rules()):
        if len(probs) != 10 or min(probs) < 0 or abs(sum(probs) - 1) > 1e-6:
            raise ValueError("probs must be 10 non-negative odds (A,2..9,ten) summing to 1")
        self.cards = [(v + 1, p) for v, p in enumerate(probs) if p > 0]
        self.probs = tuple(probs)
        self.rules = rules
        self.h17 = rules.hit_soft_17
        self._memo: dict = {}

    # ---- dealer ----------------------------------------------------------

    def _dealer_from(self, total: int, soft: bool) -> tuple[float, ...]:
        """Distribution over DEALER_OUTCOMES when the dealer plays on from a hand."""
        if total > 21:
            return (0, 0, 0, 0, 0, 1.0)
        if total > 17 or (total == 17 and not (soft and self.h17)):
            return tuple(1.0 if total == o else 0.0 for o in DEALER_OUTCOMES)
        key = ("d", total, soft)
        if key not in self._memo:
            out = [0.0] * 6
            for card, p in self.cards:
                sub = self._dealer_from(*add_card(total, soft, card))
                for i in range(6):
                    out[i] += p * sub[i]
            self._memo[key] = tuple(out)
        return self._memo[key]

    def dealer_blackjack_odds(self, up: int) -> float:
        if up == 1:
            return self.probs[9]
        return self.probs[0] if up == 10 else 0.0

    def dealer_distribution(self, up: int) -> tuple[float, ...]:
        """Dealer final-total odds given the upcard, knowing there is no blackjack."""
        key = ("up", up)
        if key not in self._memo:
            blackjack_hole = {1: 10, 10: 1}.get(up)
            hole = [(c, p) for c, p in self.cards if c != blackjack_hole]
            norm = sum(p for _, p in hole)
            start = add_card(0, False, up)
            out = [0.0] * 6
            for card, p in hole:
                sub = self._dealer_from(*add_card(*start, card))
                for i in range(6):
                    out[i] += p / norm * sub[i]
            self._memo[key] = tuple(out)
        return self._memo[key]

    # ---- player ----------------------------------------------------------

    def stand_odds(self, total: int, up: int) -> WPL:
        if total > 21:
            return (0.0, 0.0, 1.0)
        win = push = lose = 0.0
        for outcome, p in zip(DEALER_OUTCOMES, self.dealer_distribution(up)):
            if outcome == 22 or total > outcome:
                win += p
            elif total == outcome:
                push += p
            else:
                lose += p
        return (win, push, lose)

    def _draw_then(self, total: int, soft: bool, follow) -> WPL:
        """Average `follow(new_total, new_soft)` over the next card."""
        win = push = lose = 0.0
        for card, p in self.cards:
            w, u, l = follow(*add_card(total, soft, card))
            win, push, lose = win + p * w, push + p * u, lose + p * l
        return (win, push, lose)

    def hit_odds(self, total: int, soft: bool, up: int) -> WPL:
        """Take one card, then keep playing hit/stand optimally."""
        key = ("h", total, soft, up)
        if key not in self._memo:
            self._memo[key] = self._draw_then(
                total, soft, lambda t, s: self.play_odds(t, s, up))
        return self._memo[key]

    def play_odds(self, total: int, soft: bool, up: int) -> WPL:
        """Best of hit/stand from here on."""
        stand = self.stand_odds(total, up)
        if total >= 21:
            return stand
        hit = self.hit_odds(total, soft, up)
        return hit if _ev(hit) > _ev(stand) else stand

    def double_odds(self, total: int, soft: bool, up: int) -> WPL:
        """Exactly one more card, then stand (the stake is doubled by the caller)."""
        return self._draw_then(total, soft, lambda t, s: self.stand_odds(t, up))

    def split_ev(self, pair: int, up: int) -> tuple[float, WPL]:
        """EV of splitting a pair (no resplit; split aces get one card each).

        Returns (total EV across both hands, per-hand win/push/lose odds).
        """
        start = add_card(0, False, pair)
        ev = win = push = lose = 0.0
        for card, p in self.cards:
            total, soft = add_card(*start, card)
            if pair == 1:
                odds = self.stand_odds(total, up)
                hand_ev = _ev(odds)
            else:
                odds = self.play_odds(total, soft, up)
                hand_ev = _ev(odds)
                if self.rules.double_after_split:
                    dbl = self.double_odds(total, soft, up)
                    if 2 * _ev(dbl) > hand_ev:
                        odds, hand_ev = dbl, 2 * _ev(dbl)
            ev += p * hand_ev
            win, push, lose = win + p * odds[0], push + p * odds[1], lose + p * odds[2]
        return 2 * ev, (win, push, lose)

    # ---- decisions -------------------------------------------------------

    def advise_values(self, values: list[int], up: int) -> Advice:
        total, soft = hand_total(values)
        notes = []
        insurance = None
        if up == 1:
            # Insurance pays 2:1 when the hole card is a ten.
            insurance = 3 * self.probs[9] - 1
            if insurance > 0:
                notes.append(f"TAKE insurance: the shoe is ten-rich (EV {insurance:+.1%}).")
            else:
                notes.append(f"Decline insurance / even money (EV {insurance:+.1%}).")

        if total > 21:
            return Advice("bust", {}, total, False, 0.0, 0.0, 1.0, notes, insurance)
        if len(values) == 2 and total == 21:
            pay = self.rules.blackjack_payout
            return Advice("blackjack", {"blackjack": pay}, 21, True, 1.0, 0.0, 0.0,
                          notes, insurance)

        odds: dict[str, WPL] = {"stand": self.stand_odds(total, up)}
        evs = {"stand": _ev(odds["stand"])}
        if total < 21:
            odds["hit"] = self.hit_odds(total, soft, up)
            evs["hit"] = _ev(odds["hit"])
        if len(values) == 2:
            odds["double"] = self.double_odds(total, soft, up)
            evs["double"] = 2 * _ev(odds["double"])
            if values[0] == values[1]:
                evs["split"], odds["split"] = self.split_ev(values[0], up)
            if self.rules.surrender:
                evs["surrender"] = -0.5
                odds["surrender"] = (0.0, 0.0, 1.0)

        best = max(evs, key=evs.get)
        if best == "split":
            notes.append("Win/push/lose odds are per split hand.")
        if best == "surrender":
            fallback = max((a for a in evs if a != "surrender"), key=evs.get)
            notes.append(f"Surrender: give up half the bet. If not offered, "
                         f"{ACTION_NAMES[fallback]}.")
        elif best == "double":
            fallback = max(("hit", "stand"), key=evs.get)
            notes.append(f"If doubling is not allowed, {ACTION_NAMES[fallback]}.")
        win, push, lose = odds[best]
        return Advice(best, evs, total, soft, win, push, lose, notes, insurance)

    def game_ev(self) -> float:
        """Player's expected result on the next hand, per unit bet, with perfect play.

        Positive means the player has the edge at this shoe composition.
        """
        total_ev = 0.0
        for up, p_up in self.cards:
            p_dbj = self.dealer_blackjack_odds(up)
            for c1, p1 in self.cards:
                for c2, p2 in self.cards:
                    if c2 < c1:
                        continue
                    weight = p_up * p1 * p2 * (1 if c1 == c2 else 2)
                    natural = {c1, c2} == {1, 10}
                    # Dealer blackjack: everything loses except a player natural.
                    ev = p_dbj * (0.0 if natural else -1.0)
                    if p_dbj < 1:
                        ev += (1 - p_dbj) * self.advise_values([c1, c2], up).ev
                    total_ev += weight * ev
        return total_ev


def advise(player: list[str], dealer: str, rules: Rules = Rules(),
           probs: tuple[float, ...] = FULL_DECK) -> Advice:
    """Recommend the best action for `player` cards against the dealer upcard.

    `probs` are the draw odds for the next card (A, 2..9, ten); pass the
    remaining-shoe composition to get count-adjusted advice.
    """
    if len(player) < 2:
        raise ValueError("need at least two player cards")
    values = [card_value(c) for c in player]
    return Engine(probs, rules).advise_values(values, card_value(dealer))


def format_advice(player: list[str], dealer: str, advice: Advice) -> str:
    kind = "soft" if advice.soft and advice.total < 21 else "hard"
    lines = [f"You: {' '.join(player)}  ({kind} {advice.total})    Dealer: {dealer}"]
    if advice.action == "bust":
        lines.append(">>> BUST")
    elif advice.action == "blackjack":
        lines.append(f">>> BLACKJACK!  pays {advice.ev:+.2f} bets")
    else:
        lines.append(f">>> {advice.label}   (EV {advice.ev:+.3f} bets)")
        if advice.action != "surrender":
            lines.append(
                f"    win {advice.win:.1%}  push {advice.push:.1%}  lose {advice.lose:.1%}")
        ranked = sorted(advice.evs.items(), key=lambda kv: -kv[1])
        lines.append("    " + "   ".join(f"{ACTION_NAMES[a].lower()} {ev:+.3f}" for a, ev in ranked))
    lines += [f"    {n}" for n in advice.notes]
    return "\n".join(lines)
