#!/usr/bin/env python3
"""Tkinter desktop front end for dcc_mapgen.

Controls on the left, the generated Markdown and the map previews on the right.
Maps are rasterized from the generator's own SVG by resvg-py -- the same renderer
--image already uses -- so zooming re-renders from the vector source rather than
scaling pixels, and a continent-sized map stays readable when you zoom into it.
Without resvg-py installed everything still works except the inline preview, which
falls back to opening the map in a browser.

Run with:  python dcc_mapgen_gui.py
"""

import base64
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog, messagebox, ttk

import dcc_mapgen as mg

try:
    import resvg_py  # type: ignore
except ImportError:
    resvg_py = None

# Which optional controls apply to which floor. Everything not listed here is
# either universal (floor/size/seed) or unused by that floor's generator, and
# passing it anyway would just be ignored -- but hiding it keeps the panel honest
# about what actually affects the map in front of you.
FLOOR_OPTIONS = {
    1:  {"scaling", "days", "stairs"},
    2:  {"scaling", "days", "stairs"},
    3:  {"scaling", "days"},
    4:  {"days"},
    5:  {"days"},
    6:  {"scaling", "days"},
    8:  {"days", "region"},
    9:  {"days", "floor9"},
    10: {"floor10"},
    11: set(),
}

FLOOR_LABELS = {
    1: "1 - The Crawl", 2: "2 - The Scavenger Hunt", 3: "3 - The Over City",
    4: "4 - The Iron Tangle", 5: "5 - The Hunting Grounds", 6: "6 - Apocalypse",
    8: "8 - Ghosts of Earth", 9: "9 - The Siege", 10: "10 - The Faction Wars",
    11: "11 - A Parade of Horribles",
}

PAD = 8


def svg_size(svg: str) -> tuple:
    """Natural pixel size from the SVG root element."""
    w = re.search(r'<svg[^>]*?\bwidth="([\d.]+)', svg)
    h = re.search(r'<svg[^>]*?\bheight="([\d.]+)', svg)
    return (float(w.group(1)) if w else 800.0, float(h.group(1)) if h else 600.0)


def map_title(suffix: str) -> str:
    """Turn a filename suffix from floor_svgs() into something worth reading."""
    if not suffix:
        return "Main map"
    if suffix.startswith("_room_"):
        return f"Rooms - neighborhood {suffix[len('_room_'):]}"
    if suffix.startswith("_heat"):
        return f"Heat {suffix[len('_heat'):]}"
    return suffix.lstrip("_").replace("_", " ").capitalize()


class MapView(ttk.Frame):
    """Scrollable, zoomable preview of one SVG.

    Rasterizing is done off the main thread -- the biggest floor 1 maps take a
    couple of seconds at 1:1 -- and every request carries a token so that results
    from a zoom the user has already moved on from get dropped instead of
    flickering onto the canvas out of order."""

    ZOOMS = [0.1, 0.15, 0.25, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]

    def __init__(self, master, status, post, idle):
        super().__init__(master)
        self.status = status
        self.post = post          # hand a callable back to the main thread
        self.idle = idle          # the caller's resting status line, restored after a render
        self.svg = None
        self.zoom = None          # None until the first fit
        self._photo = None        # Tk drops an image that nothing references
        self._token = 0

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=PAD, pady=(PAD, 0))
        self.picker = ttk.Combobox(bar, state="readonly", width=32)
        self.picker.pack(side="left")
        self.picker.bind("<<ComboboxSelected>>", lambda e: self._on_pick())

        ttk.Button(bar, text="-", width=3, command=lambda: self._step(-1)).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="+", width=3, command=lambda: self._step(1)).pack(side="left")
        ttk.Button(bar, text="Fit", width=5, command=self.fit).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="1:1", width=5, command=lambda: self.set_zoom(1.0)).pack(side="left")
        self.zoom_lbl = ttk.Label(bar, text="", width=8, anchor="e")
        self.zoom_lbl.pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="Open in browser", command=self.open_browser).pack(side="right")

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.canvas = tk.Canvas(wrap, background="#f4f4f4", highlightthickness=1,
                                highlightbackground="#cccccc")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self._grab)
        self.canvas.bind("<B1-Motion>", self._pan)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self.canvas.configure(cursor=""))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Control-MouseWheel>", self._wheel_zoom)

        self.maps = []
        self._placeholder("Generate a floor to see its maps here.")

    # -- content ------------------------------------------------------------

    def load(self, svgs):
        """Show a freshly generated set of (suffix, svg) maps."""
        self.maps = list(svgs)
        names = [map_title(s) for s, _ in self.maps]
        self.picker.configure(values=names)
        if names:
            self.picker.current(0)
            self.svg = self.maps[0][1]
            self.fit()
        else:
            self.svg = None
            self.picker.set("")
            self._placeholder("This floor has no maps.")

    def _on_pick(self):
        i = self.picker.current()
        if 0 <= i < len(self.maps):
            self.svg = self.maps[i][1]
            self.fit()

    # -- zoom ---------------------------------------------------------------

    def fit(self):
        if not self.svg:
            return
        w, h = svg_size(self.svg)
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        if cw < 60 or ch < 60:       # called before the canvas has been laid out
            self.after(60, self.fit)
            return
        self.set_zoom(min(cw / w, ch / h))

    def set_zoom(self, z):
        self.zoom = max(0.02, min(8.0, z))
        self._render()

    def _step(self, direction):
        if self.zoom is None:
            return
        opts = self.ZOOMS if direction > 0 else list(reversed(self.ZOOMS))
        nxt = next((z for z in opts if (z > self.zoom * 1.02 if direction > 0
                                        else z < self.zoom * 0.98)), None)
        if nxt:
            self.set_zoom(nxt)

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _wheel_zoom(self, event):
        self._step(1 if event.delta > 0 else -1)
        return "break"

    def _grab(self, event):
        self.canvas.configure(cursor="fleur")
        self.canvas.scan_mark(event.x, event.y)

    def _pan(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    # -- rendering ----------------------------------------------------------

    def _placeholder(self, msg):
        self.canvas.delete("all")
        self.canvas.create_text(20, 20, anchor="nw", text=msg, fill="#666",
                                font=("Segoe UI", 10), width=520, tags="ph")
        self.canvas.configure(scrollregion=(0, 0, 0, 0))
        self.zoom_lbl.configure(text="")

    def _render(self):
        if not self.svg:
            return
        self.zoom_lbl.configure(text=f"{self.zoom * 100:.0f}%")
        if resvg_py is None:
            self._placeholder(
                "Inline preview needs resvg-py:\n\n    pip install resvg-py\n\n"
                "Everything else works without it -- use \"Open in browser\" to view this "
                "map, and save maps as SVG.")
            return

        self._token += 1
        token = self._token
        svg, zoom = self.svg, self.zoom
        self.status.set("Rendering map...")

        def work():
            try:
                png = bytes(resvg_py.svg_to_bytes(svg_string=svg, zoom=zoom))
                data = base64.b64encode(png).decode("ascii")
            except Exception as e:                        # noqa: BLE001 - shown to the user
                self.post(lambda: self._failed(token, e))
                return
            self.post(lambda: self._done(token, data))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, token, data):
        if token != self._token:
            return                                        # a newer zoom already won
        self._photo = tk.PhotoImage(data=data)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, self._photo.width(), self._photo.height()))
        self.status.set(self.idle())

    def _failed(self, token, err):
        if token != self._token:
            return
        self._placeholder(f"Could not render this map:\n\n{err}")
        self.status.set("Render failed")

    def open_browser(self):
        if not self.svg:
            return
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".svg", prefix="dcc_map_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.svg)
        webbrowser.open("file://" + path.replace("\\", "/"))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DCC Mapgen")
        self.geometry("1280x820")
        self.minsize(980, 620)
        self.result = None
        self._resting = "Ready"      # status line to fall back to once a render finishes

        # tkinter is not thread-safe, so worker threads never touch a widget: they
        # push a callable onto this queue and the main thread runs it from _poll().
        self._q = queue.Queue()

        self.status = tk.StringVar(value="Ready")
        self._build_menu()

        outer = ttk.Panedwindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True)

        self.controls = ttk.Frame(outer, width=300)
        self.controls.pack_propagate(False)
        outer.add(self.controls, weight=0)

        right = ttk.Notebook(outer)
        outer.add(right, weight=1)

        self.view = MapView(right, self.status, self.post, lambda: self._resting)
        right.add(self.view, text="Maps")

        txt_tab = ttk.Frame(right)
        right.add(txt_tab, text="Text")
        self.text = tk.Text(txt_tab, wrap="word", font=("Consolas", 10),
                            background="#fbfbfb", relief="flat", padx=10, pady=10)
        tvs = ttk.Scrollbar(txt_tab, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=tvs.set)
        tvs.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.notebook = right

        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w",
                  padding=(PAD, 3)).pack(fill="x", side="bottom")

        self._build_controls()
        self._sync_floor()
        self.after(50, self._poll)

    def post(self, fn):
        """Queue `fn` to run on the main thread. Safe to call from any thread."""
        self._q.put(fn)

    def _poll(self):
        while True:
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:                              # noqa: BLE001
                traceback.print_exc()
        self.after(50, self._poll)

    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Generate", accelerator="Ctrl+G", command=self.generate)
        f.add_command(label="Save output...", accelerator="Ctrl+S", command=self.save)
        f.add_separator()
        f.add_command(label="Re-roll rooms from a saved floor...", command=self.reroll_rooms)
        f.add_separator()
        f.add_command(label="Quit", command=self.destroy)
        m.add_cascade(label="File", menu=f)
        self.configure(menu=m)
        self.bind("<Control-g>", lambda e: self.generate())
        self.bind("<Control-s>", lambda e: self.save())

    # -- controls -----------------------------------------------------------

    def _build_controls(self):
        c = self.controls
        pad = dict(padx=PAD, pady=(0, 6), sticky="ew")
        c.columnconfigure(0, weight=1)
        row = 0

        ttk.Label(c, text="Floor", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, padx=PAD, pady=(PAD, 2), sticky="w")
        row += 1
        self.floor = tk.StringVar(value=FLOOR_LABELS[1])
        fc = ttk.Combobox(c, textvariable=self.floor, state="readonly",
                          values=[FLOOR_LABELS[f] for f in mg.FLOORS])
        fc.grid(row=row, column=0, **pad)
        fc.bind("<<ComboboxSelected>>", lambda e: self._sync_floor())
        row += 1

        ttk.Label(c, text="Size", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, padx=PAD, pady=(0, 2), sticky="w")
        row += 1
        self.size = tk.StringVar(value="medium")
        self.size_cb = ttk.Combobox(c, textvariable=self.size, state="readonly")
        self.size_cb.grid(row=row, column=0, **pad)
        row += 1

        ttk.Label(c, text="Seed", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, padx=PAD, pady=(0, 2), sticky="w")
        row += 1
        seed_row = ttk.Frame(c)
        seed_row.grid(row=row, column=0, **pad)
        self.seed = tk.StringVar()
        ttk.Entry(seed_row, textvariable=self.seed).pack(side="left", fill="x", expand=True)
        ttk.Button(seed_row, text="Clear", width=6,
                   command=lambda: self.seed.set("")).pack(side="left", padx=(4, 0))
        row += 1
        ttk.Label(c, text="Blank rolls a new seed each time. The seed of the last\n"
                          "generated floor is filled in here so you can re-roll it.",
                  foreground="#666", font=("Segoe UI", 8)).grid(row=row, column=0, padx=PAD, pady=(0, PAD), sticky="w")
        row += 1

        # Floor-specific blocks, all built once and shown or hidden per floor.
        self.opt_frames = {}
        self.opt_row0 = row

        f = self._opt_frame("scaling", "Mob levels")
        self.scaling = tk.StringVar(value="distance")
        ttk.Combobox(f, textvariable=self.scaling, state="readonly",
                     values=["distance", "random"]).pack(fill="x")

        f = self._opt_frame("days", "Countdown")
        ttk.Label(f, text="Days before the floor closes (blank = random)").pack(anchor="w")
        self.days = tk.StringVar()
        ttk.Entry(f, textvariable=self.days).pack(fill="x", pady=(2, 0))

        f = self._opt_frame("stairs", "Stairwells and bosses")
        self.boss_stairs = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Stairwells in borough/city boss chambers",
                        variable=self.boss_stairs).pack(anchor="w")
        ttk.Label(f, text="Neighborhood stairwell chance").pack(anchor="w", pady=(6, 0))
        self.stair_chance = tk.DoubleVar(value=1.0)
        self._slider(f, self.stair_chance)
        ttk.Label(f, text="Borough boss rate").pack(anchor="w", pady=(6, 0))
        self.borough_rate = tk.DoubleVar(value=1.0)
        self._slider(f, self.borough_rate)

        f = self._opt_frame("region", "Folklore region")
        self.region = tk.StringVar(value="(random)")
        ttk.Combobox(f, textvariable=self.region, state="readonly",
                     values=["(random)"] + list(mg.FOLKLORE)).pack(fill="x")

        f = self._opt_frame("floor9", "The siege")
        ttk.Label(f, text="Warlord names").pack(anchor="w")
        self.warlords = tk.StringVar(value="canon")
        ttk.Combobox(f, textvariable=self.warlords, state="readonly",
                     values=["canon", "generated"]).pack(fill="x", pady=(2, 0))
        self.wildcard = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Wildcard faction instead of the crawler team",
                        variable=self.wildcard).pack(anchor="w", pady=(6, 0))
        ttk.Label(f, text="Projected winner (blank = crawler team)").pack(anchor="w", pady=(6, 0))
        self.winner = tk.StringVar()
        ttk.Entry(f, textvariable=self.winner).pack(fill="x", pady=(2, 0))
        ttk.Label(f, text="Projected runner-up").pack(anchor="w", pady=(6, 0))
        self.runner_up = tk.StringVar()
        ttk.Entry(f, textvariable=self.runner_up).pack(fill="x", pady=(2, 0))

        f = self._opt_frame("floor10", "The race")
        ttk.Label(f, text="Vehicle").pack(anchor="w")
        self.vehicle = tk.StringVar(value="(random)")
        ttk.Combobox(f, textvariable=self.vehicle, state="readonly",
                     values=["(random)", "mechanical", "biological"]).pack(fill="x", pady=(2, 0))
        ttk.Label(f, text="Party size (2-4)").pack(anchor="w", pady=(6, 0))
        self.party = tk.IntVar(value=2)
        ttk.Spinbox(f, from_=2, to=4, textvariable=self.party, state="readonly").pack(fill="x", pady=(2, 0))

        # Buttons pinned to the bottom so they never move as options swap in and out.
        btns = ttk.Frame(c)
        btns.grid(row=99, column=0, padx=PAD, pady=PAD, sticky="ews")
        c.rowconfigure(98, weight=1)
        self.gen_btn = ttk.Button(btns, text="Generate", command=self.generate)
        self.gen_btn.pack(fill="x", ipady=4)
        self.save_btn = ttk.Button(btns, text="Save output...", command=self.save, state="disabled")
        self.save_btn.pack(fill="x", pady=(6, 0))

    def _opt_frame(self, key, title):
        f = ttk.Labelframe(self.controls, text=title, padding=PAD)
        self.opt_frames[key] = f
        return f

    def _slider(self, parent, var):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 0))
        lbl = ttk.Label(row, text=f"{var.get():.2f}", width=5, anchor="e")
        ttk.Scale(row, from_=0.0, to=1.0, variable=var, orient="horizontal",
                  command=lambda v: lbl.configure(text=f"{float(v):.2f}")).pack(
                      side="left", fill="x", expand=True)
        lbl.pack(side="left", padx=(4, 0))

    def _floor_num(self):
        return next(n for n, lbl in FLOOR_LABELS.items() if lbl == self.floor.get())

    def _sync_floor(self):
        """Keep the size list and the visible options honest about the chosen floor."""
        floor = self._floor_num()

        sizes = list(mg.SIZE_CFG) if floor in (1, 2) else [s for s in mg.SIZE_CFG if s not in mg.BIG_SIZES]
        self.size_cb.configure(values=sizes)
        if self.size.get() not in sizes:
            self.size.set("medium")

        wanted = FLOOR_OPTIONS[floor]
        row = self.opt_row0
        for key, frame in self.opt_frames.items():
            frame.grid_forget()
            if key in wanted:
                frame.grid(row=row, column=0, padx=PAD, pady=(0, PAD), sticky="ew")
                row += 1

    # -- actions ------------------------------------------------------------

    def _kwargs(self):
        """Read the panel into generate() arguments, raising MapgenError on bad input."""
        floor = self._floor_num()
        opts = FLOOR_OPTIONS[floor]

        def as_int(var, name):
            raw = var.get().strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                raise mg.MapgenError(f"{name} must be a whole number (got {raw!r})")

        kw = dict(floor=floor, size=self.size.get(), seed=as_int(self.seed, "Seed"))
        if "scaling" in opts:
            kw["scaling"] = self.scaling.get()
        if "days" in opts:
            kw["days"] = as_int(self.days, "Countdown days")
        if "stairs" in opts:
            kw.update(boss_stairs=self.boss_stairs.get(),
                      stair_chance=round(self.stair_chance.get(), 3),
                      borough_boss_rate=round(self.borough_rate.get(), 3))
        if "region" in opts and self.region.get() != "(random)":
            kw["region"] = self.region.get()
        if "floor9" in opts:
            kw.update(warlords=self.warlords.get(), wildcard=self.wildcard.get(),
                      winner=self.winner.get().strip() or None,
                      runner_up=self.runner_up.get().strip() or None)
        if "floor10" in opts:
            kw.update(party=self.party.get())
            if self.vehicle.get() != "(random)":
                kw["vehicle"] = self.vehicle.get()
        return kw

    def generate(self):
        try:
            kw = self._kwargs()
        except mg.MapgenError as e:
            messagebox.showerror("Check the options", str(e), parent=self)
            return

        self.gen_btn.configure(state="disabled")
        self.status.set("Generating...")

        def work():
            try:
                res = mg.generate(**kw)
            except mg.MapgenError as e:
                msg = str(e)
                self.post(lambda: self._gen_failed(msg))
            except Exception:                              # noqa: BLE001
                tb = traceback.format_exc()
                self.post(lambda: self._gen_failed(tb, crash=True))
            else:
                self.post(lambda: self._gen_done(res))

        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, msg):
        """Set the status line and make it the message renders fall back to."""
        self._resting = msg
        self.status.set(msg)

    def _gen_done(self, res):
        self.result = res
        self.gen_btn.configure(state="normal")
        self.save_btn.configure(state="normal")
        self.seed.set(str(res.seed))                       # so the same map can be re-rolled

        self.text.delete("1.0", "end")
        self.text.insert("1.0", res.markdown)
        self.view.load(res.svgs)
        self._set_status(f"{res.basename()} - {len(res.svgs)} map(s)")

    def _gen_failed(self, msg, crash=False):
        self.gen_btn.configure(state="normal")
        self._set_status("Generation failed")
        if crash:
            messagebox.showerror("Generator error",
                                 "The generator hit an unexpected error:\n\n" + msg, parent=self)
        else:
            messagebox.showerror("Check the options", msg, parent=self)

    def save(self):
        if not self.result:
            return
        res = self.result
        fmt = "png" if resvg_py else "svg"
        folder = filedialog.askdirectory(parent=self, title="Save Markdown and maps into...")
        if not folder:
            return

        base = os.path.join(folder, res.basename())
        md_path = base + ".md"
        img_path = base + "." + fmt
        # Check the real target names, not just the base: floor 10 writes only
        # _heat1..n and never the bare base name, so checking that alone would have
        # let a re-save clobber a folder without asking.
        targets = [md_path] + [f"{base}{suffix}.{fmt}" for suffix, _ in res.svgs]
        existing = [p for p in targets if os.path.exists(p)]
        if existing and not messagebox.askyesno(
                "Overwrite?",
                f"{len(existing)} file(s) in this folder will be replaced, including:\n\n"
                + "\n".join(os.path.basename(p) for p in existing[:6])
                + ("\n..." if len(existing) > 6 else "")
                + "\n\nContinue?", parent=self):
            return

        self.save_btn.configure(state="disabled")
        self.status.set(f"Saving {len(res.svgs)} map(s) as {fmt.upper()}...")

        def work():
            try:
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(res.markdown)
                paths = mg.save_svgs(res.svgs, img_path)
            except (mg.MapgenError, OSError) as e:
                msg = str(e)
                self.post(lambda: self._save_failed(msg))
            else:
                n = 1 + len(paths)
                self.post(lambda: self._save_done(folder, n))

        threading.Thread(target=work, daemon=True).start()

    def _save_done(self, folder, count):
        self.save_btn.configure(state="normal")
        self._set_status(f"Saved {count} file(s) to {folder}")

    def _save_failed(self, msg):
        self.save_btn.configure(state="normal")
        self._set_status("Save failed")
        messagebox.showerror("Could not save", msg, parent=self)

    def reroll_rooms(self):
        """Re-roll the room detail of one neighborhood in a floor you already generated,
        which is the GUI side of --rooms-for: useful mid-campaign when the party reaches
        a neighborhood you only ever generated at the overview level."""
        path = filedialog.askopenfilename(
            parent=self, title="Pick a floor Markdown file this app wrote",
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")])
        if not path:
            return
        label = _ask_label(self)
        if label is None:
            return
        try:
            floor = self._floor_num() if self._floor_num() in (1, 2) else 1
            nb, res = mg.regenerate_rooms(path, label or None, floor)
        except mg.MapgenError as e:
            messagebox.showerror("Could not re-roll rooms", str(e), parent=self)
            return
        except Exception:                                  # noqa: BLE001
            messagebox.showerror("Could not re-roll rooms",
                                 "That file could not be parsed as a floor 1-2 Markdown "
                                 "file from this app.\n\n" + traceback.format_exc(), parent=self)
            return

        self.result = res
        self.save_btn.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", res.markdown)
        self.view.load(res.svgs)
        self._set_status(f"Re-rolled rooms for {nb.label}. {nb.name} (seed {res.seed})")


def _ask_label(parent):
    """Which neighborhood letter to re-roll; empty means the first one in the file."""
    dlg = tk.Toplevel(parent)
    dlg.title("Which neighborhood?")
    dlg.transient(parent)
    dlg.resizable(False, False)
    ttk.Label(dlg, text="Neighborhood letter (blank = the first one in the file):",
              padding=PAD).pack(anchor="w")
    var = tk.StringVar()
    ent = ttk.Entry(dlg, textvariable=var, width=12)
    ent.pack(padx=PAD, anchor="w")
    out = {}
    btns = ttk.Frame(dlg, padding=PAD)
    btns.pack(fill="x")

    def ok():
        out["v"] = var.get().strip().upper()
        dlg.destroy()

    ttk.Button(btns, text="OK", command=ok).pack(side="right")
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=(0, 4))
    ent.bind("<Return>", lambda e: ok())
    ent.focus_set()
    dlg.grab_set()
    parent.wait_window(dlg)
    return out.get("v")


def selftest() -> int:
    """Drive the real App through a generate and check the results arrived.

    This lives in the app rather than beside it so that `dcc_mapgen_gui --self-test`
    exercises the *frozen* build: a Tk that PyInstaller failed to bundle only shows
    up when the packaged binary runs, never when the source does. Returns an exit
    code, and is pumped with update() rather than mainloop() so it can drive the app
    and still return -- generation and rasterizing hand their results back through
    the app's queue, which the pump is what drains.
    """
    timeout = 120

    def say(msg):
        # A --windowed PyInstaller build has no stdout to write to, and the exit
        # code is what CI checks anyway, so never let logging sink the test.
        try:
            print(msg)
        except Exception:                                  # noqa: BLE001
            pass

    app = App()
    app.update()

    def pump(seconds, until):
        end = time.time() + seconds
        while time.time() < end:
            app.update()
            if until():
                return True
            time.sleep(0.02)
        return False

    failures = []

    def check(cond, msg):
        say(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    say("floor 1 / large / seed 4242")
    app.floor.set(FLOOR_LABELS[1])
    app._sync_floor()
    app.size.set("large")
    app.seed.set("4242")
    app.update()
    app.generate()

    if not pump(timeout, lambda: app.result is not None):
        say("  FAIL generate finished")
        app.destroy()
        return 1
    say("  ok   generate finished")

    res = app.result
    check(res.seed == 4242, f"honored the seed (got {res.seed})")
    check(len(res.svgs) == 17, f"17 maps for a large floor 1 (got {len(res.svgs)})")
    check(len(res.markdown) > 5000, f"markdown written ({len(res.markdown)} chars)")
    check(len(app.text.get("1.0", "end")) > 5000, "markdown reached the Text tab")
    check(app.seed.get() == "4242", "seed box shows the seed used")

    if resvg_py is None:
        say("  skip  preview render (resvg-py not installed)")
    else:
        check(pump(timeout, lambda: app.view._photo is not None), "preview rasterized")
        if app.view._photo is not None:
            check(app.view._photo.width() > 100,
                  f"preview is a real image ({app.view._photo.width()}px wide)")

    # An option combination the generator must reject rather than crash on.
    errs = []
    messagebox.showerror = lambda title, msg, **kw: errs.append(msg)
    app.floor.set(FLOOR_LABELS[3])
    app._sync_floor()
    app.size.set("continent")
    app.update()
    app.generate()
    pump(30, lambda: bool(errs))
    check(bool(errs), "bad floor/size combination is reported, not crashed on")

    app.destroy()
    say(("FAILED: " + "; ".join(failures)) if failures else "gui ok")
    return 1 if failures else 0


def main():
    if "--self-test" in sys.argv[1:]:
        sys.exit(selftest())
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
