#!/usr/bin/env python3
"""Live monitor for the running Soulseek fetch.

The client is a CLI tool, so there is nothing to click. This puts a window on
the desktop showing what it is actually doing: how much of the batch is done,
what is downloading right now, total size on disk, and a scrolling tail of the
client log.

It is strictly opt-in. Nothing in the download path launches it, there is no
daemon and no autostart entry, so removing it is the same as never having had
it -- see UNDO below.

    scripts/monitor.py                     # small corner widget
    scripts/monitor.py --big               # full window with the log pane
    scripts/monitor.py --out DIR --source lists/my_tracks.csv

UNDO
----
Nothing to uninstall. The monitor only exists while you run it:

    scripts/monitor.py --close            # closes any open monitor window
    pkill -f scripts/monitor.py           # same thing, from a terminal
    sudo rm scripts/monitor.py            # only if you want the file gone

UNDO in the system sense -- never start it at login:

    # there is no autostart entry to remove. If you added one yourself:
    #   remove the monitor line from ~/.config/hypr/autostart.lua
    #   then: hyprctl reload
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
LOG = HOME / "logs" / "sldl.log"
INDEX = "_index.csv"
AUDIO = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac", ".opus"}
NOW = re.compile(r"Downloading:\s*(.+?)\s+from\s+'")

STATE_DONE = "3"
REASON_NO_RESULTS = "9"

# The palette. Green is finished, Tiffany is in flight, yellow is a track the
# network half-has, red is a dead end, purple is the "we have no idea yet"
# bucket. Every state a track can be in is one of these five.
COL = {
    "done":    "#2ee06a",   # green       - file on disk, non-empty
    "running": "#7fe3e0",   # Tiffany     - downloading right now
    "partial": "#e8c547",   # yellow      - candidate seen, no finished file
    "failed":  "#e5484d",   # red         - searched, nothing usable
    "unknown": "#a06cd5",   # purple      - not attempted yet
}
DARK = {
    "bg": "#0e0e10", "panel": "#17181d", "entry": "#0a0b0d",
    "fg": "#eaf2ea", "muted": "#6f747c", "border": "#2a2f36",
    "trough": "#131519",
}


def indexes(root: Path) -> list[Path]:
    try:
        return sorted((p for p in root.rglob(INDEX) if p.is_file()),
                      key=lambda p: p.stat().st_mtime)
    except OSError:
        return []


def manifest(root: Path) -> dict[tuple[str, str], dict]:
    """Every _index.csv under the output root, newest wins per track."""
    out: dict[tuple[str, str], dict] = {}
    for path in indexes(root):
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    key = ((row.get("artist") or "").strip(),
                           (row.get("title") or "").strip())
                    prev = out.get(key)
                    if prev is None or row.get("state") == STATE_DONE:
                        out[key] = row
        except OSError:
            pass
    return out


def job_dirs(root: Path) -> list[Path]:
    dirs = [root]
    try:
        dirs += [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        pass
    return dirs


def row_done(row: dict, root: Path) -> bool:
    rel = (row.get("filepath") or "").lstrip("./")
    if not rel or row.get("failurereason") == REASON_NO_RESULTS:
        return False
    for base in job_dirs(root):
        try:
            p = base / rel
            if p.is_file() and p.stat().st_size > 0:
                return True
        except OSError:
            pass
    return False


def audio_files(root: Path) -> list[Path]:
    try:
        return [p for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO]
    except OSError:
        return []


def dir_size(root: Path) -> int:
    total = 0
    try:
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def tally(root: Path) -> dict[str, int]:
    """Count the batch into the five colours.

    'unknown' is whatever the manifest has never mentioned, so the numbers add
    up to the batch total instead of only counting what was tried.
    """
    rows = manifest(root)
    counts = {k: 0 for k in COL}
    counts["unknown"] = max(0, root_counts(root) - len(rows))
    for row in rows.values():
        if row_done(row, root):
            counts["done"] += 1
        elif row.get("state") == STATE_DONE or row.get("state") == "1":
            counts["partial"] += 1
        else:
            counts["failed"] += 1
    return counts


def root_counts(root: Path) -> int:
    """Total size of the batch, taken from the source list when we can find it."""
    return TOTAL[0]


def current_track() -> str:
    try:
        with LOG.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-400:]
    except OSError:
        return "waiting for the client to log something"
    for line in reversed(lines):
        m = NOW.search(line)
        if m:
            return m.group(1)
        if "succeeded" in line:
            return "between tracks"
    return "idle"


def client_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", "extracted/sockseek"],
                             capture_output=True, text=True, timeout=5)
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def tail_log(lines: int = 200) -> list[str]:
    try:
        with LOG.open("r", encoding="utf-8", errors="replace") as fh:
            return [l.rstrip("\n") for l in fh.readlines()[-lines:]]
    except OSError:
        return ["no client log yet"]


TOTAL = [0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HOME / "downloads",
                    help="output directory to watch (default: ./downloads)")
    ap.add_argument("--total", type=int, default=0,
                    help="batch size; counted from the source list if omitted")
    ap.add_argument("--source", type=Path, default=None,
                    help="the CSV/list that was fed to fetch.py")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--big", action="store_true", help="full window with the log pane")
    ap.add_argument("--close", action="store_true",
                    help="close any running monitor and exit")
    ap.add_argument("--width", type=int, default=360)
    ap.add_argument("--height", type=int, default=250)
    args = ap.parse_args()

    if args.close:
        subprocess.run(["pkill", "-f", "scripts/monitor.py"], check=False)
        print("monitor: closed")
        return 0

    total = args.total
    if not total and args.source:
        try:
            sys.path.insert(0, str(HOME / "src"))
            import sldl_parser as parser
            jobs = parser.normalize_input(str(args.source)).get("jobs", [])
            total = sum(1 for j in jobs if j.get("kind") == "Song")
        except Exception as exc:  # noqa: BLE001
            print(f"monitor: could not count the batch: {exc}", file=sys.stderr)
    TOTAL[0] = total

    import tkinter as tk
    from tkinter import scrolledtext, ttk

    root = tk.Tk()
    root.title("space-seeker")
    if args.big:
        root.geometry("920x640+40+40")
    else:
        w, h = args.width, args.height
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{max(0, sw - w - 12)}+{max(0, sh - h - 60)}")
        root.attributes("-topmost", True)
    root.configure(bg=DARK["bg"])
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
    except tk.TclError:
        style = None
    if style:
        style.configure("Bar.Horizontal.TProgressbar", background=COL["done"],
                        troughcolor=DARK["trough"], borderwidth=0)
        style.configure("TLabel", background=DARK["bg"], foreground=DARK["fg"])

    head = tk.Frame(root, bg=DARK["panel"])
    head.pack(side="top", fill="x", padx=8, pady=(6, 4))
    tk.Label(head, text="space-seeker", anchor="w", bg=DARK["panel"],
             fg=COL["done"], font=("", 11, "bold")).pack(side="left")
    state_lbl = tk.Label(head, text="", anchor="e", bg=DARK["panel"],
                         fg=DARK["muted"], font=("", 9))
    state_lbl.pack(side="right")

    count_lbl = tk.Label(root, text="", anchor="w", bg=DARK["bg"],
                         fg=DARK["fg"], font=("", 9))
    count_lbl.pack(fill="x", padx=10, pady=(8, 2))

    bar = ttk.Progressbar(root, style="Bar.Horizontal.TProgressbar",
                          maximum=max(total, 1))
    bar.pack(fill="x", padx=10, pady=(0, 8))

    # one row per colour, so the state of the batch is readable at a glance
    legend = tk.Frame(root, bg=DARK["bg"])
    legend.pack(fill="x", padx=10, pady=(0, 6))
    chips: dict[str, tuple[str, tk.Label]] = {}
    for key in ("done", "running", "partial", "failed", "unknown"):
        row = tk.Frame(legend, bg=DARK["bg"])
        row.pack(fill="x", pady=1)
        tk.Label(row, text="\u25cf", bg=DARK["bg"], fg=COL[key],
                 font=("DejaVu Sans Mono", 9)).pack(side="left")
        name = {"done": "done", "running": "running", "partial": "partial",
                "failed": "failed", "unknown": "unattempted"}[key]
        lbl = tk.Label(row, text=f"{name:<12} 0", anchor="w", bg=DARK["bg"],
                       fg=COL[key], font=("DejaVu Sans Mono", 9))
        lbl.pack(side="left")
        chips[key] = (name, lbl)

    now_lbl = tk.Label(root, text="", anchor="w", bg=DARK["bg"],
                       fg=COL["running"], font=("DejaVu Sans Mono", 8),
                       wraplength=args.width - 28, justify="left")
    now_lbl.pack(fill="x", padx=10, pady=(4, 6))

    log = None
    if args.big:
        log = scrolledtext.ScrolledText(root, font=("DejaVu Sans Mono", 9),
                                        bg=DARK["entry"], fg=DARK["muted"],
                                        insertbackground=DARK["fg"],
                                        relief="flat", state="disabled")
        log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    seen = 0
    last_running: str | None = None

    def tick():
        nonlocal seen, last_running
        counts = tally(args.out)
        n = counts["done"]
        size = dir_size(args.out)
        pct = (n / total * 100) if total else 0
        bar["value"] = n
        alive = client_running()
        state_lbl.configure(text="RUNNING" if alive else "stopped",
                            fg=COL["running"] if alive else DARK["muted"])
        count_lbl.configure(text=f"{n}/{total or '?'}  {pct:.0f}%  {human(size)}")

        now = current_track()
        if alive and now != last_running:
            last_running = now
            # the one track in flight is the Tiffany row
            counts["running"] = 0 if now in ("idle", "between tracks") else 1
        for key, (name, lbl) in chips.items():
            lbl.configure(text=f"{name:<12} {counts[key]}")

        now_lbl.configure(text="fetching: " + now)

        if log is not None:
            lines = tail_log()
            if len(lines) != seen:
                log.configure(state="normal")
                log.delete("1.0", "end")
                log.insert("1.0", "\n".join(lines[-200:]))
                log.see("end")
                log.configure(state="disabled")
                seen = len(lines)
        root.after(int(args.interval * 1000), tick)

    tick()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
