"""Blackjack advisor. Run with no arguments to open the window."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .counting import Shoe, Tracker
from .strategy import FULL_DECK, Engine, Rules, advise, format_advice

DEFAULT_CONFIG = Path("blackjack_regions.json")


def _rules(args) -> Rules:
    return Rules(
        hit_soft_17=args.h17,
        double_after_split=not args.no_das,
        surrender=not args.no_surrender,
        blackjack_payout=args.bj_payout,
    )


def _parse_box(text: str) -> list[int]:
    box = [int(v) for v in text.split(",")]
    if len(box) != 4:
        raise argparse.ArgumentTypeError("region must be x,y,w,h")
    return box


# --------------------------------------------------------------------------
# advise: cards typed by hand
# --------------------------------------------------------------------------

def cmd_advise(args) -> int:
    print(format_advice(args.cards, args.dealer, advise(args.cards, args.dealer, _rules(args))))
    return 0


# --------------------------------------------------------------------------
# image: analyse a saved screenshot (also handy for checking recognition)
# --------------------------------------------------------------------------

def cmd_image(args) -> int:
    import cv2

    from .vision import read_ranks

    img = cv2.imread(args.path)
    if img is None:
        print(f"cannot read image: {args.path}", file=sys.stderr)
        return 1

    def crop(box):
        x, y, w, h = box
        return img[y:y + h, x:x + w]

    dealer = read_ranks(crop(args.dealer))
    player = read_ranks(crop(args.player))
    others = [rank for box in args.others for rank in read_ranks(crop(box))]
    print(f"dealer: {dealer or 'no cards'}   you: {player or 'no cards'}"
          + (f"   other players: {others}" if args.others else ""))
    rules = _rules(args)
    if args.no_count:
        print(_advice_text(player, dealer, rules))
        return 0
    tracker = Tracker(args.decks)
    tracker.observe({"dealer": dealer, "player": player, "others": others})
    shoe = tracker.current_shoe()
    print(_advice_text(player, dealer, rules, shoe.probs()))
    print(_count_text(shoe, rules))
    return 0


def _advice_text(player: list[str], dealer: list[str], rules: Rules,
                 probs: tuple[float, ...] = FULL_DECK) -> str:
    if not player and not dealer:
        return "Waiting for cards..."
    if "?" in player or (dealer and dealer[0] == "?"):
        return f"Can't read a card (you: {' '.join(player)}  dealer: {' '.join(dealer)})"
    if len(player) < 2 or not dealer:
        return f"Dealing...  (you: {' '.join(player) or '-'}  dealer: {' '.join(dealer) or '-'})"
    if len(dealer) > 1:
        return f"Round over  (you: {' '.join(player)}  dealer: {' '.join(dealer)})"
    return format_advice(player, dealer[0], advise(player, dealer[0], rules, probs))


def _count_text(shoe: Shoe, rules: Rules) -> str:
    """Count summary and the player's edge on a hand dealt from this shoe."""
    edge = Engine(shoe.probs(), rules).game_ev()
    if edge > 0.005:
        verdict = "shoe favours YOU: raise your bet"
    elif edge > 0:
        verdict = "about even: small bet"
    else:
        verdict = "house has the edge: minimum bet"
    return (f"Count: running {shoe.running_count:+d}   true {shoe.true_count:+.1f}   "
            f"{shoe.decks_left:.1f} decks left ({shoe.cards_seen} cards seen)\n"
            f"Next-hand edge: {edge:+.2%}  ({verdict})")


# --------------------------------------------------------------------------
# calibrate: drag two boxes on a screenshot
# --------------------------------------------------------------------------

def cmd_calibrate(args) -> int:
    from .gui import enable_dpi_awareness, select_regions

    enable_dpi_awareness()
    boxes = select_regions(None, args.monitor)
    if boxes is None:
        print("Calibration cancelled.")
        return 1
    args.config.write_text(json.dumps(boxes, indent=2))
    print(f"Saved regions to {args.config}: {boxes}")
    return 0


# --------------------------------------------------------------------------
# run: watch the screen
# --------------------------------------------------------------------------

def cmd_run(args) -> int:
    if not args.no_overlay:
        try:
            from .gui import App

            app = App(args.config, _rules(args), args.decks, not args.no_count, args.interval)
        except Exception as exc:  # no display / no tkinter: fall back to the terminal
            print(f"(window unavailable: {exc}; printing to the terminal instead)")
        else:
            app.run()
            return 0
    return _run_in_terminal(args)


def _run_in_terminal(args) -> int:
    from .gui import enable_dpi_awareness, open_screen_capture
    from .vision import grab, read_ranks

    if not args.config.exists():
        print(f"{args.config} not found - run `python -m blackjack_bot calibrate` first.",
              file=sys.stderr)
        return 1
    enable_dpi_awareness()
    regions = json.loads(args.config.read_text())
    rules = _rules(args)
    tracker = None if args.no_count else Tracker(args.decks)
    sct = open_screen_capture()
    pending = shown = None
    print("Watching the screen. Ctrl+C to stop.")
    try:
        while True:
            areas = {"dealer": read_ranks(grab(sct, regions["dealer"])),
                     "player": read_ranks(grab(sct, regions["player"]))}
            for index, box in enumerate(regions.get("others", [])):
                areas[f"other{index}"] = read_ranks(grab(sct, box))
            seen = tuple(tuple(ranks) for ranks in areas.values())
            # Require two identical reads in a row so mid-animation frames are ignored.
            stable, pending = seen == pending, seen
            if stable and seen != shown:
                shown = seen
                probs = FULL_DECK
                if tracker is not None:
                    tracker.observe(areas)
                    shoe = tracker.current_shoe()
                    probs = shoe.probs()
                text = _advice_text(areas["player"], areas["dealer"], rules, probs)
                if tracker is not None:
                    text += "\n" + _count_text(shoe, rules)
                    if tracker.overdrawn:
                        text += "\n!! Saw more cards than the shoe holds - reshuffled?"
                print(text + "\n", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    rules = argparse.ArgumentParser(add_help=False)
    group = rules.add_argument_group("table rules")
    group.add_argument("--h17", action="store_true", help="dealer hits soft 17 (default: stands)")
    group.add_argument("--no-das", action="store_true", help="no double after split")
    group.add_argument("--no-surrender", action="store_true", help="surrender not offered")
    group.add_argument("--bj-payout", type=float, default=1.5,
                       help="blackjack payout: 1.5 for 3:2 (default), 1.2 for 6:5")

    count = argparse.ArgumentParser(add_help=False)
    group = count.add_argument_group("card counting")
    group.add_argument("--decks", type=int, default=6, help="decks in the shoe (default 6)")
    group.add_argument("--no-count", action="store_true",
                       help="ignore seen cards (for games that reshuffle every hand)")

    parser = argparse.ArgumentParser(prog="blackjack_bot", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("calibrate", help="mark the dealer and player card areas on screen")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--monitor", type=int, default=1, help="monitor number (1 = primary)")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("run", parents=[rules, count], help="open the advisor window (the default)")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--interval", type=float, default=0.4, help="seconds between screen reads")
    p.add_argument("--no-overlay", action="store_true", help="print to the terminal only")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("advise", parents=[rules], help="get advice for cards you type in")
    p.add_argument("cards", nargs="+", help="your cards, e.g. A 7")
    p.add_argument("--dealer", "-d", required=True, help="dealer upcard, e.g. 6")
    p.set_defaults(func=cmd_advise)

    p = sub.add_parser("image", parents=[rules, count], help="analyse a saved screenshot")
    p.add_argument("path")
    p.add_argument("--dealer", type=_parse_box, required=True, metavar="x,y,w,h")
    p.add_argument("--player", type=_parse_box, required=True, metavar="x,y,w,h")
    p.add_argument("--others", type=_parse_box, action="append", default=[], metavar="x,y,w,h",
                   help="another player's card area (repeatable)")
    p.set_defaults(func=cmd_image)

    if argv is None:
        argv = sys.argv[1:]
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["run", *argv]  # no command: just open the window
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
