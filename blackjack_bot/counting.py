"""Shoe tracking: remember every card seen and expose what is left.

The tracker watches the table over many frames.  Within a round cards only
ever get added, so the round's cards are the per-area maximum of what was
seen; when cards disappear the round is over and they are removed from the
shoe for good.
"""

from __future__ import annotations

from collections import Counter

from .strategy import FULL_DECK, card_value

HI_LO = {1: -1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 0, 8: 0, 9: 0, 10: -1}


class Shoe:
    """Cards remaining in an n-deck shoe, by value (1 = ace ... 10 = tens/faces)."""

    def __init__(self, decks: int = 6):
        if decks < 1:
            raise ValueError("decks must be at least 1")
        self.decks = decks
        self.reset()

    def reset(self) -> None:
        self.remaining = {v: 4 * self.decks for v in range(1, 10)}
        self.remaining[10] = 16 * self.decks
        self.running_count = 0

    def remove(self, value: int) -> bool:
        """Take one card out. Returns False if the shoe has none left of it."""
        if self.remaining[value] <= 0:
            return False
        self.remaining[value] -= 1
        self.running_count += HI_LO[value]
        return True

    @property
    def cards_left(self) -> int:
        return sum(self.remaining.values())

    @property
    def cards_seen(self) -> int:
        return 52 * self.decks - self.cards_left

    @property
    def decks_left(self) -> float:
        return self.cards_left / 52

    @property
    def true_count(self) -> float:
        return self.running_count / max(self.decks_left, 0.25)

    def probs(self) -> tuple[float, ...]:
        """Odds of each value being the next card drawn."""
        left = self.cards_left
        if left == 0:
            return FULL_DECK
        return tuple(self.remaining[v] / left for v in range(1, 11))

    def without(self, values) -> "Shoe":
        """Copy of this shoe with extra cards removed (cards on the table now)."""
        copy = Shoe(self.decks)
        copy.remaining = dict(self.remaining)
        copy.running_count = self.running_count
        for value in values:
            copy.remove(value)
        return copy


class Tracker:
    """Turns successive readings of the table into a running shoe count."""

    def __init__(self, decks: int = 6):
        self.shoe = Shoe(decks)  # excludes the round in progress
        self.round: dict[str, Counter] = {}
        self.visible = 0
        self.rounds_counted = 0
        self.overdrawn = False  # saw more of a card than the shoe holds: missed a shuffle

    def reset(self) -> None:
        """Call when the dealer shuffles."""
        self.shoe.reset()
        self.round = {}
        self.visible = 0
        self.overdrawn = False

    def observe(self, areas: dict[str, list[str]]) -> None:
        """Feed one stable reading: area name -> ranks visible there ('?' = unread)."""
        visible = sum(len(ranks) for ranks in areas.values())
        if visible < self.visible:  # cards were collected: the round is over
            self._commit()
        self.visible = visible
        for name, ranks in areas.items():
            seen = Counter(card_value(r) for r in ranks if r != "?")
            current = self.round.setdefault(name, Counter())
            for value, count in seen.items():
                current[value] = max(current[value], count)

    def _round_values(self) -> list[int]:
        return [v for counter in self.round.values() for v in counter.elements()]

    def _commit(self) -> None:
        values = self._round_values()
        for value in values:
            if not self.shoe.remove(value):
                self.overdrawn = True
        if values:
            self.rounds_counted += 1
        self.round = {}

    def current_shoe(self) -> Shoe:
        """The shoe as it stands right now, including cards on the table."""
        return self.shoe.without(self._round_values())
