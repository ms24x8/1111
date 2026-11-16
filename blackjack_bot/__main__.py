"""Command line entry point: python -m blackjack_bot <command>."""

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


def _enable_dpi_awareness() -> None:
    """Make window coordinates match screenshot pixels on scaled Windows displays."""
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


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
    import tkinter as tk

    import mss
    from PIL import Image, ImageTk

    _enable_dpi_awareness()
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
        monitor = sct.monitors[args.monitor]
        shot = sct.grab(monitor)
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    view_w, view_h = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = max(image.width / view_w, image.height / view_h)
    shown = image.resize((round(image.width / scale), round(image.height / scale)))
    photo = ImageTk.PhotoImage(shown)
    canvas = tk.Canvas(root, width=view_w, height=view_h, highlightthickness=0, cursor="cross")
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor="nw")

    steps = [("dealer's", "#ff4040"), ("your own", "#40c0ff")]
    boxes: dict[str, list[int]] = {}
    others: list[list[int]] = []
    canvas.create_rectangle(0, 0, view_w, 44, fill="black", outline="")
    banner = canvas.create_text(view_w // 2, 22, fill="yellow", font=("Arial", 15, "bold"))
    state = {"start": None, "rect": None}

    def prompt():
        if len(boxes) < len(steps):
            name = steps[len(boxes)][0]
            text = f"Drag a box around the {name.upper()} card area   (Esc = cancel)"
        else:
            text = (f"Now box each OTHER PLAYER's cards ({len(others)} added)   "
                    "Enter or right-click = done")
        canvas.itemconfigure(banner, text=text)

    def press(event):
        state["start"] = (event.x, event.y)
        colour = steps[len(boxes)][1] if len(boxes) < len(steps) else "#60ff60"
        state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                outline=colour, width=3)

    def drag(event):
        if state["start"]:
            canvas.coords(state["rect"], *state["start"], event.x, event.y)

    def release(event):
        if not state["start"]:
            return
        (x0, y0), state["start"] = state["start"], None
        x1, y1 = event.x, event.y
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 10 or h < 10:
            canvas.delete(state["rect"])
            return
        box = [
            monitor["left"] + round(min(x0, x1) * scale),
            monitor["top"] + round(min(y0, y1) * scale),
            round(w * scale),
            round(h * scale),
        ]
        if len(boxes) < len(steps):
            boxes[("dealer", "player")[len(boxes)]] = box
        else:
            others.append(box)
        prompt()

    def finish(_event):
        if len(boxes) == len(steps):
            root.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    def cancel(_event):
        boxes.clear()
        root.destroy()

    root.bind("<Escape>", cancel)
    root.bind("<Return>", finish)
    canvas.bind("<ButtonPress-3>", finish)
    root.focus_force()
    prompt()
    root.mainloop()

    if len(boxes) < len(steps):
        print("Calibration cancelled.")
        return 1
    boxes["others"] = others
    args.config.write_text(json.dumps(boxes, indent=2))
    print(f"Saved regions to {args.config}: {boxes}")
    return 0


# --------------------------------------------------------------------------
# run: watch the screen
# --------------------------------------------------------------------------

def cmd_run(args) -> int:
    import mss

    from .vision import grab, read_ranks

    if not args.config.exists():
        print(f"{args.config} not found - run `python -m blackjack_bot calibrate` first.",
              file=sys.stderr)
        return 1
    _enable_dpi_awareness()
    regions = json.loads(args.config.read_text())
    rules = _rules(args)
    tracker = None if args.no_count else Tracker(args.decks)
    sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    state = {"pending": None, "shown": None}

    def poll() -> str | None:
        """Return new text to show, or None if nothing changed."""
        areas = {"dealer": read_ranks(grab(sct, regions["dealer"])),
                 "player": read_ranks(grab(sct, regions["player"]))}
        for index, box in enumerate(regions.get("others", [])):
            areas[f"other{index}"] = read_ranks(grab(sct, box))
        seen = tuple(tuple(ranks) for ranks in areas.values())
        # Require two identical reads in a row so mid-animation frames are ignored.
        stable = seen == state["pending"]
        state["pending"] = seen
        if not stable or seen == state["shown"]:
            return None
        state["shown"] = seen
        if tracker is None:
            return _advice_text(areas["player"], areas["dealer"], rules)
        tracker.observe(areas)
        shoe = tracker.current_shoe()
        text = _advice_text(areas["player"], areas["dealer"], rules, shoe.probs())
        text += "\n" + _count_text(shoe, rules)
        if tracker.overdrawn:
            text += "\n!! Saw more cards than the shoe holds - reshuffled? Reset the count."
        return text

    def reset():
        if tracker is not None:
            tracker.reset()
            state["shown"] = None
            print("-- count reset: new shoe --\n")

    window = None
    if not args.no_overlay:
        try:
            window = _Overlay(reset if tracker is not None else None)
        except Exception as exc:  # no display / no tkinter: fall back to the terminal
            print(f"(overlay unavailable: {exc}; printing to the terminal instead)")

    print("Watching the screen. Ctrl+C to stop.")
    try:
        if window:
            def tick():
                text = poll()
                if text:
                    print(text + "\n")
                    window.show(text)
                window.root.after(int(args.interval * 1000), tick)

            tick()
            window.root.mainloop()
        else:
            while True:
                text = poll()
                if text:
                    print(text + "\n", flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


class _Overlay:
    """Small always-on-top window showing the current advice."""

    def __init__(self, on_reset=None):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.title("Blackjack advisor")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#101418")
        self.action = tk.Label(self.root, text="Waiting...", font=("Arial", 28, "bold"),
                               fg="#ffd84a", bg="#101418", padx=16, pady=6)
        self.action.pack(anchor="w")
        self.detail = tk.Label(self.root, text="", font=("Courier", 12), justify="left",
                               fg="#e8e8e8", bg="#101418", padx=16, pady=8)
        self.detail.pack(anchor="w")
        if on_reset:
            tk.Button(self.root, text="New shoe (dealer shuffled): reset count",
                      command=on_reset).pack(fill="x", padx=16, pady=(0, 12))

    def show(self, text: str) -> None:
        lines = text.split("\n")
        headline = next((ln[4:] for ln in lines if ln.startswith(">>> ")), lines[0])
        rest = [ln.strip() for ln in lines if not ln.startswith(">>> ")]
        self.action.configure(text=headline.split("   ")[0])
        self.detail.configure(text="\n".join(rest) if headline != lines[0] else "")


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
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("calibrate", help="mark the dealer and player card areas on screen")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--monitor", type=int, default=1, help="monitor number (1 = primary)")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("run", parents=[rules, count], help="watch the screen and show the best play")
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

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
