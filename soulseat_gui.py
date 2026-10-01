#!/usr/bin/env python3
"""Soulseek - drag-and-drop GUI for the existing /data/soulseek Soulseek setup.

Behaviour contract (matches the strict scope for this deployment):
  * Reads inputs ONLY from /data/soulseek/ and never touches the local
    config (/data/soulseek/config/sldl.conf) or credentials.
  * Playlist/text files are accepted from the command line (the desktop
    launcher passes them via %F), the "Add Playlist" chooser, or by
    dropping them onto the launcher. Adding/viewing a file NEVER starts a
    download and performs no network access.
  * DOWNLOAD is the only way to start a download session. It invokes
        /data/soulseek/run.sh run <playlist>
    for every queued playlist. run.sh hard-wires --output-dir
    /data/soulseek/downloads/ and the existing 320 kbps condition; this GUI
    never bypasses run.sh, never passes output/quality overrides, and never
    uses a shell. Playlist contents are parsed as data only (stdlib, offline)
    and are never executed.
  * --dry-run maps DOWNLOAD onto "run.sh dry-run <playlist>" - the existing
    zero-network test mechanism - and is used for testing only.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import threading
import queue
from pathlib import Path

RUN_HOME = "/data/soulseek"
RUN_SH = os.path.join(RUN_HOME, "run.sh")
PARSER_SRC = os.path.join(RUN_HOME, "src")


# ---------------------------------------------------------------------------
# Dark theme (explicit colors / fonts; no reliance on the desktop theme).
# ---------------------------------------------------------------------------

DARK = {
    "bg": "#0e0e10",          # window / frame / root background
    "panel": "#17181d",       # headings / status bar background
    "panel_alt": "#22242c",   # button hover / raised areas
    "entry": "#0a0b0d",       # text / list / log areas
    "fg": "#eef3ef",          # primary text
    "heading_fg": "#e6ece8",  # column headings / status text
    "log_fg": "#d5e0d9",      # status-log text
    "muted": "#8b9299",       # disabled / secondary text
    "accent": "#155a2e",      # selection background
    "accent_fg": "#ffffff",   # selection foreground
    "btn": "#1d2026",         # button background
    "btn_active": "#2c3a2c",  # button hover
    "border": "#2a2f36",      # focus outlines / separators
    "trough": "#131519",      # scrollbar trough
}

# Space Bunny Feture state colours. Green bunny while downloading, reels that
# turn red and stop when it lands, a jetpack that stays white, an orange flame.
BUNNY = {
    "body": "#3ddc84",        # bunny, green in every state
    "pack": "#f2f5f3",        # jetpack tanks, white
    "trim": "#7f8a86",        # plate, straps, fins, nozzle
    "flame": "#ff8c1a",       # exhaust, only while working
    "flame_core": "#ffd166",  # hotter core inside the flame
    "wheel": "#f5d033",        # 45s while turning, and while idle
    "wheel_done": "#e0483c",  # 45s stopped: red
}

# Font sizes scale with the window instead of being fixed pixel counts, so the
# UI stays readable on a HiDPI panel and in a small window. BASE_SCALE is the
# multiplier applied on top of the window's own scaling.
BASE_SCALE = 1.0
SCALE_MIN, SCALE_MAX = 0.75, 2.25
_watch = []          # widget + option-database Font objects kept alive
_scale_bindings = []  # (root, callback) pairs re-applied on resize

FONT = {
    "base": ("VT323", 24),
    "list": ("VT323", 26),
    "button": ("VT323", 26),
    "heading": ("VT323", 28),
    "mono": ("VT323", 25),
}

# VT323 is the dot-matrix face this deployment asks for. It has to exist, or the
# GUI silently falls back to a proportional face and the whole look is lost.
UI_FONT = "VT323"

# Height of the circuit-board strip pinned along the top edge.
BANNER_H = 78


def _ui_scale(root):
    """Scale factor derived from the window's pixel size.

    Tk reports the window in pixels, so a HiDPI screen yields a large value
    even though the logical font size should grow with it. 720p is treated as
    the 1.0 reference.
    """
    try:
        h = root.winfo_screenheight()
    except Exception:  # noqa: BLE001
        h = 1080
    factor = max(SCALE_MIN, min(SCALE_MAX, (h / 1080.0) * BASE_SCALE))
    return factor


def _resolve_fonts(root):
    """Pick installed families and size them relative to the screen.

    Registers named fonts on the option database so dialogs and message boxes
    pick up the same sizing as the main window.
    """
    import tkinter.font as tkfont
    families = set(tkfont.families(root))
    mono = UI_FONT if UI_FONT in families else next(
        (f for f in ("DejaVu Sans Mono", "Noto Sans Mono",
                     "Liberation Mono")
         if f in families), "TkFixedFont")
    sans = mono
    scale = _ui_scale(root)
    for key, (_family, size) in {
            "base": (sans, 24), "list": (sans, 26),
            "button": (sans, 26), "heading": (sans, 28),
            "mono": (sans, 25)}.items():
        FONT[key] = (mono, max(11, int(round(size * scale))))
    _register_option_fonts(root, sans, scale)


def _register_option_fonts(root, sans, scale):
    import tkinter.font as tkfont
    for opt, family, size in (
            ("*Font", sans, 24), ("*Button.font", sans, 26),
            ("*Text.font", sans, 25), ("*Listbox.font", sans, 25),
            ("*TCombobox*Listbox.font", sans, 25)):
        f = tkfont.Font(root=root, family=family,
                        size=max(11, int(round(size * scale))))
        _watch.append(f)
        root.option_add(opt, f.name)


def _apply_scaling(root):
    """Re-resolve fonts on resize so the UI tracks the window, not the screen."""
    def _on_resize(_event=None):
        _resolve_fonts(root)
        for widget, key in getattr(root, "_font_targets", []):
            try:
                if key in FONT:
                    widget.configure(font=FONT[key])
            except Exception:  # noqa: BLE001 - a dead widget must not stop resize
                pass
    _scale_bindings.append((root, _on_resize))
    root.bind("<Configure>", _on_resize, add="+")
    return _on_resize


def _apply_dark_theme(root):
    """Configure root, option database, and ttk styles for the dark theme."""
    import tkinter as tk
    from tkinter import ttk

    _resolve_fonts(root)
    root.configure(bg=DARK["bg"])
    for opt, val in (
            ("*Background", DARK["bg"]),
            ("*Foreground", DARK["fg"]),
            ("*selectBackground", DARK["accent"]),
            ("*selectForeground", DARK["accent_fg"]),
            ("*insertBackground", DARK["fg"]),
            ("*activeBackground", DARK["btn_active"]),
            ("*disabledForeground", DARK["muted"])):
        root.option_add(opt, val)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=DARK["bg"], foreground=DARK["fg"],
                    font=FONT["base"], bordercolor=DARK["border"],
                    darkcolor=DARK["bg"], lightcolor=DARK["bg"],
                    troughcolor=DARK["trough"], focuscolor=DARK["accent"],
                    selectbackground=DARK["accent"],
                    selectforeground=DARK["accent_fg"])
    style.configure("Dark.Treeview", background=DARK["entry"],
                    fieldbackground=DARK["entry"], foreground=DARK["fg"],
                    font=FONT["list"], rowheight=58, borderwidth=0)
    style.map("Dark.Treeview",
              background=[("selected", DARK["accent"])],
              foreground=[("selected", DARK["accent_fg"])])
    style.configure("Dark.Treeview.Heading", background=DARK["panel"],
                    foreground=DARK["heading_fg"], font=FONT["heading"],
                    relief="flat", padding=(12, 9))
    style.map("Dark.Treeview.Heading",
              background=[("active", DARK["panel_alt"])])
    style.configure("Dark.Vertical.TScrollbar", background=DARK["btn"],
                    troughcolor=DARK["trough"], bordercolor=DARK["bg"],
                    arrowcolor=DARK["fg"], relief="flat")
    style.map("Dark.Vertical.TScrollbar",
              background=[("active", DARK["btn_active"])])
    return style


def _dark_button(parent, text, command):
    """A tk.Button styled for the dark theme."""
    import tkinter as tk
    return tk.Button(
        parent, text=text, command=command,
        bg=DARK["btn"], fg=DARK["fg"],
        activebackground=DARK["btn_active"], activeforeground=DARK["fg"],
        disabledforeground=DARK["muted"],
        relief="flat", bd=0,
        highlightthickness=1, highlightbackground=DARK["border"],
        highlightcolor=DARK["border"],
        padx=18, pady=8, font=FONT["button"], cursor="hand2",
    )


# ---------------------------------------------------------------------------
# Offline parsing (stdlib only: mirrors what run.sh dry-run / sldl parse use).
# ---------------------------------------------------------------------------

_parser = None


def _get_parser():
    """Import the existing offline parser (never used for network access)."""
    global _parser
    if _parser is None:
        sys.path.insert(0, PARSER_SRC)
        import sldl_parser as parser_mod  # noqa: PLC0415
        _parser = parser_mod
    return _parser


def absolutize(path: str) -> str:
    """Return an absolute path; reject directories and missing files."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        raise NotADirectoryError(f"not a file: {path}")
    return path


def format_job(job: dict) -> str:
    """Human-readable single line per parsed job (mirrors sldl_parser)."""
    kind = job.get("kind", "")
    if kind == "Song":
        label = " - ".join(
            p for p in (job.get("artist", ""), job.get("title", "")) if p
        ) or job.get("display", "")
        return f"Song: {label}"
    if kind == "Album":
        return "Album: " + (
            job.get("display") or job.get("album") or job.get("link", "") or ""
        )
    if "link" in job:
        return f"{kind}: {job['link']}"
    return f"{kind}: {job.get('url') or job.get('query') or ''}"


def parse_playlist(path: str) -> dict:
    """Offline-normalize a playlist/text file into a job tree (no network)."""
    return _get_parser().normalize_input(path)


def _elide(text: str, limit: int) -> str:
    """Shorten a long path from the middle, keeping both ends readable.

    A playlist added from a deep folder produced a header that ran off the
    right edge of the column, and the trailing "(N entries, type=csv)" count
    was what got cut. Eliding the middle drops the redundant middle instead.
    """
    text = str(text)
    if len(text) <= limit:
        return text
    keep = limit - 3
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}...{text[-tail:]}"


def playlist_display_lines(path: str):
    """Return a list of display lines for one playlist (file name + entries)."""
    try:
        result = parse_playlist(path)
    except Exception as exc:  # noqa: BLE001 - surfaced into the GUI status
        return [f"{_elide(path, 64)}: ERROR - {exc}"]
    singular = len(result["jobs"]) == 1
    lines = [
        f"{_elide(path, 64)}  ({len(result['jobs'])} "
        f"entr{'y' if singular else 'ies'}, type={result['inputType']})"
    ]
    for job in result["jobs"]:
        lines.append("   " + format_job(job))
    return lines


# ---------------------------------------------------------------------------
# run.sh invocation (single spawn point; never a shell).
# ---------------------------------------------------------------------------

def _download_command(path: str, dry_run: bool) -> list:
    if dry_run:
        return [RUN_SH, "dry-run", path]
    return [RUN_SH, "run", path]


def run_stream(args, cwd, on_line=None):
    """Run a command, stream combined stdout/stderr, return (rc, output)."""
    proc = subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    chunks = []
    for raw in iter(proc.stdout.readline, ""):
        if on_line:
            on_line(raw)
        chunks.append(raw)
    proc.wait()
    rc = proc.returncode
    return rc, "".join(chunks)


def execute_downloads(playlists, dry_run=False, on_line=None):
    """Run run.sh for each playlist in order. Nothing else is executed."""
    results = []
    for path in playlists:
        cmd = _download_command(path, dry_run)
        echo = " ".join(cmd)
        if on_line:
            on_line(f"$ {echo}\n")
        try:
            rc, output = run_stream(cmd, cwd=RUN_HOME, on_line=on_line)
        except Exception as exc:  # noqa: BLE001 - report as failure
            results.append((path, False, f"{exc}"))
            if on_line:
                on_line(f"[FAILURE] {path}: {exc}\n")
            continue
        ok = rc == 0
        results.append((path, ok, output))
        if on_line:
            on_line(f"[{'SUCCESS' if ok else 'FAILURE (rc=%d)' % rc}] {path}\n")
    return results


# ---------------------------------------------------------------------------
# Headless self-test mode (no Tk required; zero network).
# ---------------------------------------------------------------------------

def self_test(files):
    """Parse + display each file, then exercise the run.sh command path via
    the existing zero-network 'dry-run' mechanism only. Returns exit code."""
    print("Soulseek self-test: parsing/display only (no network, no download)")
    all_lines = []
    for path in files:
        try:
            path = absolutize(path)
        except Exception as exc:  # noqa: BLE001
            all_lines.append(f"{path}: ERROR - {exc}")
            continue
        all_lines.extend(playlist_display_lines(path))
    shown = set()
    for line in all_lines:
        head = line.split("  ", 1)[0]
        if head not in shown:
            shown.add(head)
        print("  " + line if line.startswith(" ") else line)
    print()
    print("Soulseek self-test: exercising the DOWNLOAD command path with")
    print("run.sh dry-run (existing zero-network test mechanism).")

    out_lines = []
    results = execute_downloads(files, dry_run=True, on_line=out_lines.append)
    sys.stdout.write("".join(out_lines))
    failed = [p for p, ok, _ in results if not ok]
    print()
    print(f"self-test: {len(results) - len(failed)}/{len(results)} paths OK")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# GUI (Tkinter, standard library).
# ---------------------------------------------------------------------------

class BunnyGauge:
    """The Space Bunny Feture indicator that lives in the status strip.

    The bunny rides on two 45 RPM record inserts instead of feet: a disc with a
    spindle hole and three lightening holes, which is what turns when it is
    working. Three states, told apart from across the room:

      idle     green bunny, yellow records, no flame. Nothing asked for yet.
      working  green bunny, yellow records spinning, orange flame flickering.
      done     green bunny stays, records turn red and stop, flame out.

    Everything redraws on one timer tick rather than animating per item, so the
    spin and the flicker share a clock instead of drifting across a dozen
    separate after() calls.
    """

    WIDTH, HEIGHT = 178, 104

    RECORDS = ((62, 84), (100, 84))   # 45s, where the feet would be
    DISC_R = 12
    SPIN_DEG = 14.0                   # per tick
    TICK_MS = 45
    FLICKER = (0.55, 0.85, 1.0, 0.72, 0.95, 0.62)

    def __init__(self, parent, tk):
        self.tk = tk
        self.state = "idle"
        self.angle = 0.0
        self._flick = 0
        self._job = None
        self.canvas = tk.Canvas(
            parent, width=self.WIDTH, height=self.HEIGHT,
            bg=DARK["panel"], highlightthickness=0, bd=0)
        self._draw()

    # ---- state -------------------------------------------------------------

    def set_state(self, state):
        """idle | working | done. Returns immediately; the timer does the rest."""
        if state == self.state:
            return
        self.state = state
        if state == "working":
            if self._job is None:
                self._schedule()
        else:
            self._stop()
        self._draw()

    def destroy(self):
        self._stop()

    def _schedule(self):
        self._job = self.canvas.after(self.TICK_MS, self._tick)

    def _stop(self):
        if self._job is not None:
            try:
                self.canvas.after_cancel(self._job)
            except Exception:  # noqa: BLE001 - the widget may already be gone
                pass
            self._job = None

    def _tick(self):
        self._job = None
        if self.state != "working":
            return
        self.angle = (self.angle + self.SPIN_DEG) % 360
        self._flick = (self._flick + 1) % len(self.FLICKER)
        self._draw()
        self._schedule()

    # ---- drawing -----------------------------------------------------------

    def _draw(self):
        c = self.canvas
        c.delete("all")

        working = self.state == "working"
        body = BUNNY["body"]
        pack = BUNNY["pack"]
        trim = BUNNY["trim"]
        if working:
            disc = BUNNY["wheel"]
        elif self.state == "done":
            disc = BUNNY["wheel_done"]
        else:
            disc = BUNNY["wheel"]

        self._draw_jetpack(c, pack, trim, working)
        self._draw_bunny(c, body)
        self._draw_records(c, disc)

    def _draw_jetpack(self, c, pack, trim, working):
        """Two tanks, a strapped mounting plate, cooling fins and a nozzle.

        Drawn before the bunny so the pack sits behind his back, the way it
        would actually be worn.
        """
        # mounting plate across the shoulders
        c.create_rectangle(8, 22, 42, 34, fill=trim, outline="")
        # two tanks with a highlight down the front of each
        c.create_rectangle(11, 30, 23, 62, fill=pack, outline="")
        c.create_rectangle(27, 30, 39, 62, fill=pack, outline="")
        c.create_rectangle(13, 34, 16, 58, fill=trim, outline="")
        c.create_rectangle(29, 34, 32, 58, fill=trim, outline="")
        # straps holding the tanks to the plate
        c.create_rectangle(10, 38, 40, 41, fill=trim, outline="")
        c.create_rectangle(10, 52, 40, 55, fill=trim, outline="")
        # rivets
        for rx in (12, 21, 28, 37):
            c.create_oval(rx, 24, rx + 2, 26, fill=pack, outline="")
        # cooling fins on the back edge
        for fy in (34, 42, 50):
            c.create_polygon(4, fy, 11, fy - 3, 11, fy + 3,
                             fill=trim, outline="")
        # nozzle housing and throat
        c.create_rectangle(16, 62, 34, 70, fill=pack, outline="")
        c.create_polygon(16, 70, 34, 70, 30, 76, 20, 76,
                         fill=trim, outline="")

        if working:
            # Flame: shoots a little further past the nozzle on each flicker
            # step, so it dances instead of sitting still. nozzle_x is the
            # centreline of the throat, lip_y the lip the flame leaves from.
            reach = self.FLICKER[self._flick] * 20.0
            nozzle_x, lip_y = 25.0, 76.0
            c.create_polygon(
                nozzle_x - 4, lip_y, nozzle_x + 4, lip_y,
                nozzle_x + 1.5 + reach * 0.16, lip_y + reach,
                fill=BUNNY["flame"], outline="")
            c.create_polygon(
                nozzle_x - 2, lip_y, nozzle_x + 2, lip_y,
                nozzle_x + 0.8 + reach * 0.10, lip_y + reach * 0.55,
                fill=BUNNY["flame_core"], outline="")
        else:
            c.create_rectangle(21, 74, 29, 77, fill=trim, outline="")

    def _draw_bunny(self, c, body):
        """Green bunny, facing right, ears up, eye punched back out."""
        # legs down to the records
        c.create_rectangle(55, 60, 69, 76, fill=body, outline="")
        c.create_rectangle(93, 58, 107, 74, fill=body, outline="")
        # body and haunch
        c.create_oval(40, 26, 106, 78, fill=body, outline="")
        # ears: drawn before the head, tall and narrow, so what clears the
        # skull reads as two upright ears rather than two bumps
        c.create_oval(94, -2, 104, 30, fill=body, outline="")
        c.create_oval(107, 0, 117, 30, fill=body, outline="")
        # head
        c.create_oval(98, 12, 134, 46, fill=body, outline="")
        # eye
        c.create_oval(120, 24, 126, 30, fill=DARK["panel"], outline="")

    def _draw_records(self, c, disc):
        """Two 45 RPM inserts: a disc, a spindle hole, three lightening holes.

        The three holes are what make the spin readable. A plain disc rotating
        looks identical at every angle, so the turn would not register as
        movement at all.
        """
        # tape bridging the two, tinted to match the disc
        c.create_rectangle(62, 79, 100, 83, fill=disc, outline="")
        for cx, cy in self.RECORDS:
            c.create_oval(cx - self.DISC_R, cy - self.DISC_R,
                          cx + self.DISC_R, cy + self.DISC_R,
                          fill=disc, outline="")
            # rim groove
            c.create_oval(cx - self.DISC_R + 2, cy - self.DISC_R + 2,
                          cx + self.DISC_R - 2, cy + self.DISC_R - 2,
                          fill="", outline=DARK["panel"], width=1)
            # three lightening holes, the part that shows the turn
            for k in range(3):
                a = (self.angle + k * 120.0) * math.pi / 180.0
                hx = cx + 7.0 * math.cos(a)
                hy = cy + 7.0 * math.sin(a)
                c.create_oval(hx - 2, hy - 2, hx + 2, hy + 2,
                              fill=DARK["panel"], outline="")
            # spindle hole
            c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                          fill=DARK["panel"], outline="")


class SoulseekApp:
    def __init__(self, root, initial_files, dry_run=False):
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk

        self.root = root
        self.dry_run = dry_run
        self.playlists = []          # ordered absolute paths
        self.queue = queue.Queue()   # worker -> UI messages
        self.running = False
        self._last_results = None

        root.title("Soulseek - Soulseek playlist downloads")
        root.geometry("1180x760")
        root.minsize(920, 620)

        _apply_dark_theme(root)

        self._cyber_bg_image = None
        self._cyber_bg = None
        self._cyber_bg_src = None
        self._banner_image = None
        self._banner = None
        bg_dir = Path(__file__).resolve().parent
        assets = bg_dir / "assets"
        # The window wall is the portrait circuit board, already knocked back to
        # 34% so the file list stays readable on top of it. The horizontal strip
        # along the top edge is the wide one. Both fall back to nothing rather
        # than back to the old Michelangelo hands.
        wall = self._first_readable(
            tk, [assets / "circuit_board_wall_dim.jpg",
                 assets / "circuit_board_wall_dim.png"])
        if wall is not None:
            self._place_wall(wall)
        else:
            print("Circuit-board wall not found under assets/; "
                  "window background left flat.")

        banner = self._first_readable(
            tk, [assets / "circuit_board_banner_strip.jpg",
                 assets / "circuit_board_banner_strip.png"])
        if banner is not None:
            self._banner_image = banner
            self._banner = tk.Label(root, image=banner, bd=0,
                                    anchor="center", bg=DARK["bg"])
            self._banner.place(x=0, y=0, relwidth=1, height=BANNER_H)
        bar = tk.Frame(root, padx=8, pady=6, bg=DARK["bg"])
        bar.pack(side="top", fill="x", pady=(BANNER_H + 4, 6))

        self.btn_add = _dark_button(bar, "Add Playlist...", self._pick_files)
        self.btn_add.pack(side="left")
        self.btn_clear = _dark_button(bar, "Clear", self._clear_all)
        self.btn_clear.pack(side="left", padx=(8, 0))
        self.btn_dl = _dark_button(bar, "Open Downloads", self._open_downloads)
        self.btn_dl.pack(side="left", padx=(8, 0))
        self.btn_run = _dark_button(
            bar,
            "DOWNLOAD (dry-run)" if dry_run else "DOWNLOAD",
            self._start_download,
        )
        self.btn_run.pack(side="right")

        self.status = tk.Label(
            root, anchor="w", padx=8, pady=6, bg=DARK["panel"],
            fg=DARK["heading_fg"], font=FONT["heading"],
            text="Ready. Add a playlist.",
        )
        self.status.pack(side="bottom", fill="x")

        # The bunny sits in the status strip on the right: the system-monitor
        # end of the bar, so it reads as part of the running state rather than
        # as decoration stuck in the middle of the file list.
        self.bunny = BunnyGauge(self.status, tk)
        self.bunny.canvas.pack(side="right", padx=(10, 0))

        cols = ("file", "entry")
        self.tree = ttk.Treeview(
            root, columns=cols, show="headings", style="Dark.Treeview",
        )
        self.tree.heading("file", text="Playlist")
        self.tree.heading("entry", text="Parsed entries")
        self.tree.tag_configure("folder", foreground=DARK["log_fg"])
        self.tree.column("file", width=560, stretch=True)
        self.tree.column("entry", width=580, stretch=True)
        ys = ttk.Scrollbar(root, orient="vertical",
                           command=self.tree.yview,
                           style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="top", fill="both", expand=True, padx=8, pady=(6, 0))
        ys.pack(side="right", fill="y")

        self.log = scrolledtext.ScrolledText(
            root, height=6, state="disabled", font=FONT["mono"],
            bg=DARK["entry"], fg=DARK["log_fg"], insertbackground=DARK["fg"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=DARK["border"], highlightcolor=DARK["border"],
            padx=8, pady=6,
        )
        self.log.pack(side="bottom", fill="x", padx=8, pady=8)

        for path in initial_files:
            self.add_playlist(path)

        self.root.after(100, self._pump_queue)

    # ---- status helpers ----------------------------------------------------

    def _first_readable(self, tk, candidates):
        """First candidate path this Tk build can actually decode into pixels.

        Tk 8.6 reads PNG on its own but not JPEG, so the JPEG originals go
        through ImageMagick (or Pillow) first. Returns the PhotoImage, not the
        path, because a caller wants something it can place.
        """
        for path in candidates:
            if not path.is_file():
                continue
            photo = None
            try:
                photo = tk.PhotoImage(file=path)
            except tk.TclError:
                photo = None
            if photo is None and path.suffix.lower() in (".jpg", ".jpeg"):
                photo = self._background_from_magick(tk, path)
            if photo is None:
                photo = self._background_from_pil(tk, path)
            if photo is not None:
                return photo
        return None

    def _place_wall(self, photo):
        """Lay the dimmed circuit board behind everything else in the window.

        Tk has no real alpha, so this only shows through where the widgets above
        it are not fully opaque. That is deliberate: the file list keeps its own
        solid panels and the wall reads through the gaps around them.
        """
        import tkinter as tk
        self._cyber_bg_image = photo
        self._cyber_bg_src = photo
        self._cyber_bg = tk.Label(
            self.root, image=photo, bd=0, anchor="center", bg="#04070a")
        self._cyber_bg.place(x=0, y=0, relwidth=1, relheight=1)
        self._cyber_bg.lower()
        self.root.bind("<Configure>", self._fit_background, add="+")

    def _try_background(self, tk, bg_path):
        """Place a background image behind the widgets.

        Loader chain, in order (only actually-installed tooling is used):
          1. Tk 8.6 native PNG reader (PhotoImage file=).
          2. JPEG via the installed ImageMagick binary (magick -> PNG data),
             which covers the canonical soulseek_cyber_background.jpg asset
             without requiring Pillow.
          3. Pillow, if it is ever installed.
        Returns True when placed. The path is resolved relative to this
        file, never the shell's cwd.
        """
        if not bg_path.is_file():
            return False

        photo = None
        try:
            photo = tk.PhotoImage(file=bg_path)
        except tk.TclError:
            photo = None

        if photo is None and bg_path.suffix.lower() in (".jpg", ".jpeg"):
            photo = self._background_from_magick(tk, bg_path)
        if photo is None:
            photo = self._background_from_pil(tk, bg_path)
        if photo is None:
            return False

        self._cyber_bg_image = photo
        self._cyber_bg_src = photo
        # Tk cannot do real alpha, so the image only shows where the widgets
        # above it are not fully opaque. An opaque container frame would hide
        # it completely, which is why the background looked "not implemented"
        # even though the assets and the loader were both present.
        self._cyber_bg = tk.Label(
            self.root, image=self._cyber_bg_image, bd=0, anchor="center",
            bg="#04070a")
        self._cyber_bg.place(x=0, y=0, relwidth=1, relheight=1)
        self._cyber_bg.lower()
        self.root.bind("<Configure>", self._fit_background, add="+")
        # Panels that should let the artwork through get the image's own dark
        # tone instead of an opaque fill.
        tone = self._image_tone(photo)
        if tone:
            DARK["entry"] = tone
            DARK["bg"] = tone
        return True

    @staticmethod
    def _image_tone(photo):
        """Average colour of the artwork, so panels blend into it."""
        try:
            w, h = photo.width(), photo.height()
            if w < 2 or h < 2:
                return None
            # Tk 8.6 exposes pixel values through get(); sample a sparse grid
            # rather than the whole image to stay fast.
            rs = gs = bs = n = 0
            step_y = max(1, h // 12)
            step_x = max(1, w // 12)
            for y in range(0, h, step_y):
                for x in range(0, w, step_x):
                    try:
                        r, g, b = photo.get(x, y)
                    except Exception:  # noqa: BLE001
                        continue
                    rs += r; gs += g; bs += b; n += 1
            if not n:
                return None
            return "#%02x%02x%02x" % (rs // n, gs // n, bs // n)
        except Exception:  # noqa: BLE001 - purely cosmetic
            return None

    def _fit_background(self, _event=None):
        """Keep the single background covering the whole window on resize.

        Tk can only zoom PhotoImages by whole factors, so we zoom just enough
        to cover the current window (capped to keep memory sane) and keep the
        label anchored center so the extra is cropped evenly.
        """
        if self._cyber_bg_src is None:
            return
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        if w < 40 or h < 40:
            return
        sw, sh = self._cyber_bg_src.width(), self._cyber_bg_src.height()
        z = 1
        while z < 8 and (sw * z < w or sh * z < h):
            z += 1
        if z > 1:
            fitted = self._cyber_bg_src.zoom(z)
        else:
            fitted = self._cyber_bg_src
        self._cyber_bg_image = fitted
        self._cyber_bg.configure(image=fitted)

    @staticmethod
    def _background_from_magick(tk, bg_path):
        """Read a JPEG through the installed ImageMagick binary as PNG data."""
        import base64
        try:
            proc = subprocess.run(
                ["magick", str(bg_path), "PNG:-"],
                capture_output=True, check=True, timeout=60,
            )
            return tk.PhotoImage(
                data=base64.b64encode(proc.stdout).decode("ascii"))
        except Exception:  # noqa: BLE001 - next fallback decides
            return None

    @staticmethod
    def _background_from_pil(tk, bg_path):
        """Read via Pillow, if Pillow is actually installed."""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return None
        try:
            return ImageTk.PhotoImage(Image.open(bg_path).convert("RGB"))
        except Exception:  # noqa: BLE001 - treat as "cannot display"
            return None

    def _log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---- playlist management ----------------------------------------------

    def _pick_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Add playlist(s)",
            initialdir=Path.home(),
            filetypes=(
                ("Playlists / texts", "*.csv *.list *.txt *.m3u"),
                ("All files", "*"),
            ),
        )
        for path in paths:
            self.add_playlist(path)

    def _open_downloads(self):
        downloads = Path(RUN_HOME) / "downloads"
        if not downloads.is_dir():
            self._log_line(f"[ERROR] downloads dir missing: {downloads}\n")
            return
        import subprocess as _sp
        opener = None
        for cmd in (("xdg-open",), ("gio", "open"), ("open",)):
            if _sp.run(cmd, capture_output=True).returncode == 0:
                opener = cmd
                break
        if opener is None:
            self._flash_status("No system 'open' command found.")
            self._log_line(f"Open this folder: {downloads}\n")
            return
        _sp.Popen([*opener, str(downloads)])
        self._flash_status(f"Opened {downloads}")

    def add_playlist(self, path):
        """Queue a playlist for display and later download. Never downloads."""
        try:
            path = absolutize(path)
        except Exception as exc:  # noqa: BLE001
            self._log_line(f"[ERROR] {path}: {exc}\n")
            self._flash_status("Could not add file.")
            return
        if path in self.playlists:
            self._log_line(f"already queued: {path}\n")
            return
        self.playlists.append(path)
        lines = playlist_display_lines(path)
        for index, line in enumerate(lines):
            # The file name belongs to the playlist, not to each of its rows.
            # Repeating it down every line is what made the list unreadable:
            # the column filled with one name and the actual entries vanished
            # into the noise beside it.
            name = os.path.basename(path) if index == 0 else ""
            stripped = line.strip()
            if line.startswith("   "):
                self.tree.insert(
                    "", "end",
                    values=(name, stripped),
                    tags=("folder",) if stripped.startswith(("Album:", "Folder:")) else (),
                )
            else:
                self.tree.insert("", "end", values=(name, line))
        self._flash_status(f"Added {path} (parsed offline; no download).")

    def _clear_all(self):
        if self.running:
            self._flash_status("Download in progress - Clear disabled.")
            return
        self.playlists.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._flash_status("Cleared.")

    # ---- download ----------------------------------------------------------

    def _start_download(self):
        if self.running:
            return
        if not self.playlists:
            self._flash_status("No playlists queued.")
            return
        self.running = True
        self.bunny.set_state("working")
        self.btn_run.configure(state="disabled")
        self.btn_add.configure(state="disabled")
        self._log_line(
            f"\n== DOWNLOAD start ({'dry-run' if self.dry_run else 'run'}) "
            f"via {RUN_SH} ==\n"
        )
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()

    def _worker(self):
        try:
            results = execute_downloads(self.playlists, dry_run=self.dry_run,
                                        on_line=self.queue.put)
            self.queue.put(("summary", results))
        finally:
            self.queue.put(None)

    def _pump_queue(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item is None:
                    self._download_finished(self._last_results)
                    break
                if isinstance(item, tuple) and item and item[0] == "summary":
                    self._last_results = item[1]
                    continue
                self._log_line(item)
        except queue.Empty:
            pass
        self.root.after(100, self._pump_queue)

    def _download_finished(self, results):
        self.running = False
        self.bunny.set_state("done")
        self.btn_run.configure(state="normal")
        self.btn_add.configure(state="normal")
        ok = 0
        total = len(results or [])
        for _path, success, _out in results or []:
            ok += 1 if success else 0
        self._flash_status(
            f"Finished: {ok}/{total or 0} succeeded"
            + ("." if not self.dry_run else " (dry-run, nothing downloaded).")
        )
        from tkinter import messagebox
        messagebox.showinfo(
            "Soulseek",
            f"Finished: {ok} of {total or 0} playlists OK."
            if not self.dry_run else
            f"Dry-run finished: {ok} of {total or 0} playlists parsed. "
            "Nothing was downloaded.",
        )

    def _flash_status(self, text):
        self.status.configure(text=text)


def run_gui(argv_files, dry_run=False):
    import tkinter as tk
    root = tk.Tk()
    SoulseekApp(root, argv_files, dry_run=dry_run)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="soulseat_gui",
        description="Drag-and-drop GUI for the /data/soulseek Soulseek setup.",
    )
    ap.add_argument("playlists", nargs="*", metavar="PLAYLIST",
                    help="playlist/text files (dropped onto the launcher / %%F)")
    ap.add_argument("--dry-run", action="store_true",
                    help="testing only: DOWNLOAD maps to run.sh dry-run "
                         "(zero network)")
    ap.add_argument("--self-test", action="store_true",
                    help="headless self-test: parse + display + dry-run, no GUI")
    args = ap.parse_args(argv)

    files = [f for f in args.playlists]

    if args.self_test:
        if not files:
            print("self-test: no playlist files given", file=sys.stderr)
            return 2
        return self_test(files)

    run_gui(files, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())