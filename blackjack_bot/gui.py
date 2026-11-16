"""The bot's window: shows what it sees, the move to make, the odds and the count.

Start it with `python -m blackjack_bot` (or double-click the start script).
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path

from .counting import Tracker
from .strategy import ACTION_NAMES, FULL_DECK, Engine, Rules, advise

BG, PANEL, TEXT, DIM = "#12161c", "#1c232d", "#eef2f6", "#8b98a8"
GOOD, BAD, NEUTRAL, GOLD = "#35c46a", "#e5484d", "#7d8794", "#ffd84a"
ACTION_COLOUR = {
    "hit": "#2f9e5b", "stand": "#d6453d", "double": "#2f7fd6", "split": "#8a55d8",
    "surrender": "#6b7480", "blackjack": "#c99a14", "bust": "#6b7480",
}
SMALL, BODY, MONO = ("Arial", 10), ("Arial", 12), ("Courier", 12)


def enable_dpi_awareness() -> None:
    """Make window coordinates match screenshot pixels on scaled Windows displays."""
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def open_screen_capture():
    import mss

    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def select_regions(parent: tk.Misc | None, monitor_index: int = 1) -> dict | None:
    """Let the user drag boxes over a screenshot. Returns the regions or None."""
    from PIL import Image, ImageTk

    with open_screen_capture() as sct:
        monitor = sct.monitors[monitor_index]
        shot = sct.grab(monitor)
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    window = tk.Toplevel(parent) if parent else tk.Tk()
    window.attributes("-fullscreen", True)
    window.attributes("-topmost", True)
    view_w, view_h = window.winfo_screenwidth(), window.winfo_screenheight()
    scale = max(image.width / view_w, image.height / view_h)
    shown = image.resize((round(image.width / scale), round(image.height / scale)))
    photo = ImageTk.PhotoImage(shown, master=window)
    canvas = tk.Canvas(window, width=view_w, height=view_h, highlightthickness=0, cursor="cross")
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.create_rectangle(0, 0, view_w, 44, fill="black", outline="")
    banner = canvas.create_text(view_w // 2, 22, fill="yellow", font=("Arial", 15, "bold"))

    steps = [("dealer", "the DEALER'S", "#ff4040"), ("player", "YOUR OWN", "#40c0ff")]
    boxes: dict = {}
    others: list[list[int]] = []
    state = {"start": None, "rect": None, "done": False}

    def prompt():
        if len(boxes) < len(steps):
            text = f"Drag a box around {steps[len(boxes)][1]} card area   (Esc = cancel)"
        else:
            text = (f"Now box each OTHER PLAYER's cards ({len(others)} added)   "
                    "Enter or right-click = done")
        canvas.itemconfigure(banner, text=text)

    def press(event):
        state["start"] = (event.x, event.y)
        colour = steps[len(boxes)][2] if len(boxes) < len(steps) else "#60ff60"
        state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                outline=colour, width=3)

    def drag(event):
        if state["start"]:
            canvas.coords(state["rect"], *state["start"], event.x, event.y)

    def release(event):
        if not state["start"]:
            return
        (x0, y0), state["start"] = state["start"], None
        w, h = abs(event.x - x0), abs(event.y - y0)
        if w < 10 or h < 10:
            canvas.delete(state["rect"])
            return
        box = [monitor["left"] + round(min(x0, event.x) * scale),
               monitor["top"] + round(min(y0, event.y) * scale),
               round(w * scale), round(h * scale)]
        if len(boxes) < len(steps):
            boxes[steps[len(boxes)][0]] = box
        else:
            others.append(box)
        prompt()

    def finish(_event=None):
        if len(boxes) == len(steps):
            state["done"] = True
            window.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    canvas.bind("<ButtonPress-3>", finish)
    window.bind("<Return>", finish)
    window.bind("<Escape>", lambda _e: window.destroy())
    window.focus_force()
    prompt()
    if parent:
        parent.wait_window(window)
    else:
        window.mainloop()
    if not state["done"]:
        return None
    boxes["others"] = others
    return boxes


class App:
    """Main window. Polls the screen and redraws when the table changes."""

    def __init__(self, config: Path, rules: Rules = Rules(), decks: int = 6,
                 count: bool = True, interval: float = 0.4):
        enable_dpi_awareness()
        self.config = config
        self.interval = int(interval * 1000)
        self.regions = json.loads(config.read_text()) if config.exists() else None
        self.sct = open_screen_capture()
        self.pending = self.shown = None
        self.paused = False

        root = self.root = tk.Tk()
        root.title("Blackjack advisor")
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.geometry(f"+{max(0, root.winfo_screenwidth() - 460)}+40")

        # --- the move -------------------------------------------------------
        self.action = tk.Label(root, text="", font=("Arial", 34, "bold"), fg="white",
                               bg=NEUTRAL, width=15, pady=14)
        self.action.pack(fill="x")
        self.subtitle = tk.Label(root, text="", font=BODY, fg=TEXT, bg=BG, pady=6,
                                 wraplength=420, justify="center")
        self.subtitle.pack(fill="x")

        # --- what the bot sees ---------------------------------------------
        seen = self._panel("WHAT I SEE")
        self.dealer_cards = self._row(seen, "Dealer")
        self.player_cards = self._row(seen, "You")
        self.other_cards = self._row(seen, "Other players")

        # --- odds -----------------------------------------------------------
        odds = self._panel("ODDS IF YOU FOLLOW THE ADVICE")
        self.bar = tk.Canvas(odds, height=26, bg=PANEL, highlightthickness=0)
        self.bar.pack(fill="x", padx=10, pady=(2, 0))
        self.bar_text = tk.Label(odds, text="", font=SMALL, fg=DIM, bg=PANEL)
        self.bar_text.pack(anchor="w", padx=10)
        self.moves = tk.Label(odds, text="", font=MONO, fg=TEXT, bg=PANEL, justify="left")
        self.moves.pack(anchor="w", padx=10, pady=(4, 8))

        # --- count ----------------------------------------------------------
        count_panel = self._panel("CARD COUNT")
        self.count_line = tk.Label(count_panel, text="", font=MONO, fg=TEXT, bg=PANEL,
                                   justify="left")
        self.count_line.pack(anchor="w", padx=10)
        self.bet_line = tk.Label(count_panel, text="", font=("Arial", 13, "bold"), fg=TEXT,
                                 bg=PANEL)
        self.bet_line.pack(anchor="w", padx=10, pady=(2, 8))

        # --- controls -------------------------------------------------------
        buttons = tk.Frame(root, bg=BG)
        buttons.pack(fill="x", padx=10, pady=(8, 2))
        tk.Button(buttons, text="Set up card areas", command=self.calibrate).pack(
            side="left", expand=True, fill="x", padx=2)
        tk.Button(buttons, text="New shoe (shuffle)", command=self.new_shoe).pack(
            side="left", expand=True, fill="x", padx=2)
        self.pause_button = tk.Button(buttons, text="Pause", command=self.toggle_pause)
        self.pause_button.pack(side="left", expand=True, fill="x", padx=2)

        settings = tk.Frame(root, bg=BG)
        settings.pack(fill="x", padx=10, pady=2)
        self.decks = tk.IntVar(value=decks)
        self.count = tk.BooleanVar(value=count)
        self.h17 = tk.BooleanVar(value=rules.hit_soft_17)
        self.surrender = tk.BooleanVar(value=rules.surrender)
        self.das = tk.BooleanVar(value=rules.double_after_split)
        self.six_five = tk.BooleanVar(value=rules.blackjack_payout < 1.5)
        self.custom_payout = rules.blackjack_payout
        tk.Label(settings, text="Decks", font=SMALL, fg=TEXT, bg=BG).grid(row=0, column=0,
                                                                          sticky="w")
        tk.Spinbox(settings, from_=1, to=8, width=3, textvariable=self.decks,
                   command=self.new_shoe).grid(row=0, column=1, sticky="w", padx=(4, 12))
        checks = [("Count cards", self.count), ("Dealer hits soft 17", self.h17),
                  ("Surrender allowed", self.surrender), ("Double after split", self.das),
                  ("Blackjack pays 6:5", self.six_five)]
        for index, (label, variable) in enumerate(checks):
            tk.Checkbutton(settings, text=label, variable=variable, command=self.settings_changed,
                           font=SMALL, fg=TEXT, bg=BG, selectcolor=PANEL, activebackground=BG,
                           activeforeground=TEXT, highlightthickness=0).grid(
                row=(index + 1) // 2, column=2 + (index + 1) % 2, sticky="w")

        self.status = tk.Label(root, text="", font=SMALL, fg=DIM, bg=BG, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(2, 8))

        self.tracker = Tracker(self._decks())
        self.render(None)
        root.after(200, self.tick)

    # ---- widgets -----------------------------------------------------------

    def _panel(self, title: str) -> tk.Frame:
        frame = tk.Frame(self.root, bg=PANEL)
        frame.pack(fill="x", padx=10, pady=4)
        tk.Label(frame, text=title, font=("Arial", 8, "bold"), fg=DIM, bg=PANEL).pack(
            anchor="w", padx=10, pady=(6, 2))
        return frame

    def _row(self, parent: tk.Frame, name: str) -> tk.Label:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=10, pady=1)
        tk.Label(row, text=name, font=BODY, fg=DIM, bg=PANEL, width=12, anchor="w").pack(side="left")
        value = tk.Label(row, text="-", font=("Arial", 14, "bold"), fg=TEXT, bg=PANEL, anchor="w")
        value.pack(side="left", pady=(0, 4))
        return value

    # ---- settings ----------------------------------------------------------

    def _decks(self) -> int:
        try:
            return min(8, max(1, int(self.decks.get())))
        except (tk.TclError, ValueError):
            return 6

    def rules(self) -> Rules:
        payout = 1.2 if self.six_five.get() else max(self.custom_payout, 1.5)
        return Rules(self.h17.get(), self.das.get(), self.surrender.get(), payout)

    def settings_changed(self) -> None:
        self.shown = None  # redraw with the new rules on the next read

    def new_shoe(self) -> None:
        self.tracker = Tracker(self._decks())
        self.shown = None

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.configure(text="Resume" if self.paused else "Pause")
        self.shown = None
        if self.paused:
            self.status.configure(text="Paused: not reading the screen or counting.")

    def calibrate(self) -> None:
        self.root.withdraw()  # get out of the screenshot
        self.root.after(350, self._calibrate_now)

    def _calibrate_now(self) -> None:
        try:
            regions = select_regions(self.root)
        finally:
            self.root.deiconify()
        if regions:
            self.regions = regions
            self.config.write_text(json.dumps(regions, indent=2))
            self.new_shoe()

    # ---- screen reading ----------------------------------------------------

    def tick(self) -> None:
        try:
            if self.regions and not self.paused:
                self.poll()
            elif not self.regions:
                self.render(None)
        except Exception as exc:  # keep the window alive; show what went wrong
            self.status.configure(text=f"Error reading the screen: {exc}")
        self.root.after(self.interval, self.tick)

    def poll(self) -> None:
        from .vision import grab, read_ranks

        areas = {"dealer": read_ranks(grab(self.sct, self.regions["dealer"])),
                 "player": read_ranks(grab(self.sct, self.regions["player"]))}
        for index, box in enumerate(self.regions.get("others", [])):
            areas[f"other{index}"] = read_ranks(grab(self.sct, box))
        seen = tuple(tuple(ranks) for ranks in areas.values())
        # Require two identical reads in a row so mid-animation frames are ignored.
        stable, self.pending = seen == self.pending, seen
        if not stable or seen == self.shown:
            return
        self.shown = seen
        if self.count.get():
            self.tracker.observe(areas)
        self.render(areas)

    # ---- drawing -----------------------------------------------------------

    def render(self, areas: dict | None) -> None:
        if areas is None:
            self._headline("SET UP", NEUTRAL,
                           'Open your blackjack table, then click "Set up card areas".')
            self._odds(None)
            self._count()
            self.status.configure(text="Card areas are not set yet.")
            return
        dealer, player = areas["dealer"], areas["player"]
        others = [r for name, ranks in areas.items() if name.startswith("other") for r in ranks]
        self.dealer_cards.configure(text=" ".join(dealer) or "-")
        self.player_cards.configure(text=" ".join(player) or "-")
        self.other_cards.configure(text=" ".join(others) or "-")
        counting = self.count.get()
        shoe = self.tracker.current_shoe() if counting else None
        probs = shoe.probs() if shoe else FULL_DECK
        advice = None
        if not player and not dealer:
            self._headline("WAITING", NEUTRAL, "Place your bet. Waiting for cards.")
        elif "?" in player or (dealer and dealer[0] == "?"):
            self._headline("CAN'T READ", NEUTRAL,
                           "A card is unclear or cut off, so no advice.\n"
                           "Make sure each card area fully contains the cards.")
        elif len(player) < 2 or not dealer:
            self._headline("DEALING", NEUTRAL, "Waiting for the cards to land.")
        elif len(dealer) > 1:
            self._headline("ROUND OVER", NEUTRAL, "The dealer has played.")
        else:
            advice = advise(player, dealer[0], self.rules(), probs)
            kind = "soft" if advice.soft and advice.total < 21 else "hard"
            self.player_cards.configure(text=f"{' '.join(player)}   ({kind} {advice.total})")
            if advice.action == "bust":
                self._headline("BUST", ACTION_COLOUR["bust"], "Over 21.")
            elif advice.action == "blackjack":
                self._headline("BLACKJACK", ACTION_COLOUR["blackjack"],
                               " ".join([f"Pays {advice.ev:+.2f} bets."] + advice.notes))
            else:
                detail = [f"Expected result: {advice.ev:+.3f} bets"] + advice.notes
                self._headline(advice.label, ACTION_COLOUR[advice.action], "\n".join(detail))
        self._odds(advice)
        self._count(shoe)
        unread = sum(ranks.count("?") for ranks in areas.values())
        note = f"  {unread} card(s) unreadable, not counted." if unread else ""
        self.status.configure(text="Watching the table." + note)

    def _headline(self, text: str, colour: str, subtitle: str) -> None:
        self.action.configure(text=text, bg=colour)
        self.subtitle.configure(text=subtitle)

    def _odds(self, advice) -> None:
        self.bar.delete("all")
        if advice is None or advice.action in ("bust", "blackjack", "surrender"):
            self.bar_text.configure(text="")
            self.moves.configure(text="" if advice is None or not advice.evs else self._moves(advice))
            return
        self.bar.update_idletasks()
        width, x = max(self.bar.winfo_width(), 200), 0
        for share, colour in ((advice.win, GOOD), (advice.push, NEUTRAL), (advice.lose, BAD)):
            self.bar.create_rectangle(x, 0, x + share * width, 26, fill=colour, outline="")
            x += share * width
        self.bar_text.configure(
            text=f"win {advice.win:.1%}     push {advice.push:.1%}     lose {advice.lose:.1%}"
                 + ("   (per split hand)" if advice.action == "split" else ""))
        self.moves.configure(text=self._moves(advice))

    @staticmethod
    def _moves(advice) -> str:
        ranked = sorted(advice.evs.items(), key=lambda item: -item[1])
        return "\n".join(
            f"{'>' if name == advice.action else ' '} {ACTION_NAMES.get(name, name.upper()):<10}"
            f"{ev:+.3f}" for name, ev in ranked)

    def _count(self, shoe=None) -> None:
        if shoe is None:
            self.count_line.configure(text="Counting is off." if not self.count.get() else "-")
            self.bet_line.configure(text="", fg=TEXT)
            return
        edge = Engine(shoe.probs(), self.rules()).game_ev()
        self.count_line.configure(
            text=f"Running {shoe.running_count:+d}    True {shoe.true_count:+.1f}\n"
                 f"{shoe.decks_left:.1f} decks left   {shoe.cards_seen} cards seen")
        if self.tracker.overdrawn:
            self.bet_line.configure(text="Count is off: was there a shuffle? Click New shoe.",
                                    fg=GOLD)
        elif edge > 0.005:
            self.bet_line.configure(text=f"Your edge {edge:+.2%}: RAISE your bet", fg=GOOD)
        elif edge > 0:
            self.bet_line.configure(text=f"Your edge {edge:+.2%}: about even, small bet", fg=GOLD)
        else:
            self.bet_line.configure(text=f"Your edge {edge:+.2%}: minimum bet", fg=BAD)

    def run(self) -> None:
        self.root.mainloop()
