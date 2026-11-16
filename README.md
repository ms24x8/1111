# Blackjack screen advisor

Reads the blackjack table on your screen, counts every card it sees, and tells
you the play with the best odds.

- **Pure probability.** No strategy chart is looked up. For every legal move
  (stand, hit, double, split, surrender) it computes the exact expected value
  and the win / push / lose odds from the chance of each card being drawn next.
- **Card counting.** It reads your cards, the dealer's and the other players',
  removes each one from the shoe, and recalculates the odds from the cards that
  are actually left. It also shows the Hi-Lo running and true count, whether
  insurance is worth taking, and your edge on the next hand (when to bet more).
- **Advice only.** It never clicks or bets for you.

## Install

Python 3.10+ is required.

```bash
pip install -r requirements.txt
```

On Linux the overlay window also needs Tk (`sudo apt install python3-tk python3-pil.imagetk`).

## Use

1. Open the game so the table is visible, then mark where the cards are:

   ```bash
   python -m blackjack_bot calibrate
   ```

   Drag a box around the dealer's cards, then your cards, then one box per
   other player. Press Enter (or right-click) when done. Make each box big
   enough to hold a hand after several hits.

2. Start it:

   ```bash
   python -m blackjack_bot run --decks 6
   ```

   A small always-on-top window shows the play. When the dealer shuffles, click
   **New shoe** to reset the count.

Example output:

```
You: 10 6  (hard 16)    Dealer: 10
>>> SURRENDER   (EV -0.500 bets)
    surrender -0.500   stand -0.553   hit -0.555   double -1.111
    Surrender: give up half the bet. If not offered, STAND.
Count: running +9   true +1.6   5.6 decks left (19 cards seen)
Next-hand edge: +0.37%  (about even: small bet)
```

EV is in bets: `-0.107` means you lose 10.7% of your bet on average with that move.

### Options

| Option | Meaning |
| --- | --- |
| `--decks N` | decks in the shoe (default 6) |
| `--no-count` | ignore seen cards; use for games that reshuffle every hand |
| `--h17` | dealer hits soft 17 (default: stands) |
| `--no-das` | no double after split |
| `--no-surrender` | surrender is not offered |
| `--bj-payout 1.2` | blackjack pays 6:5 instead of 3:2 |
| `--no-overlay` | print to the terminal only |
| `--interval 0.4` | seconds between screen reads |

### Other commands

```bash
python -m blackjack_bot advise 10 6 --dealer 10
```

```bash
python -m blackjack_bot image shot.png --dealer 440,40,420,260 --player 440,400,420,260 --others 20,360,400,200
```

`advise` works from cards you type; `image` analyses a saved screenshot, which
is the quickest way to check that your game's cards are recognised.

## What to know

- **Counting only helps with a real shoe** (live-dealer or physical tables).
  Software (RNG) blackjack reshuffles before every hand, so there is nothing
  to count: use `--no-count` there. Games using a continuous shuffling machine
  are the same.
- The bot cannot see a shuffle. Reset the count yourself, or it will keep
  counting against the old shoe. It warns if it sees more of a card than the
  shoe can hold.
- The count starts when the bot starts. Cards dealt before that are unknown.
- Card recognition expects light card faces with the rank in the top-left
  corner, side by side or fanned sideways. A card it is not sure about is
  shown as `?` and no advice is given, rather than guessing. It has been tested
  on rendered tables, not on every casino skin: check yours with `image` first.
- One hand of yours is read. After a split, use `advise` for each hand.
- Model details: draws are treated as independent at the current shoe
  composition, the dealer peeks for blackjack, and a pair can be split once.
- Many casinos forbid assistance tools in their terms; using one can get an
  account closed. Even perfect counting gives an edge of roughly 0.5-1%, with
  large swings.

## Tests

```bash
python -m pytest
```
