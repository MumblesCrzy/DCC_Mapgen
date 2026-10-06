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

# Floors 3-11 are named as this generator's own module docstring names them; keep
# them in step with it. Floors 1 and 2 have no name there -- they title themselves
# per seed ("The Gilded Neighborhood") -- so these two are the setting's names for
# them rather than anything the generator emits.
FLOOR_LABELS = {
    1: "1 - The Collapse",
    2: "2 - The Crawl",
    3: "3 - The Over City",
    4: "4 - The Iron Tangle",
    5: "5 - The Bubbles",
    6: "6 - The Hunting Grounds",
    8: "8 - Ghosts of Earth",
    9: "9 - Faction Wars",
    10: "10 - Don't Come In Last",
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


class CampaignView(ttk.Frame):
    """The roster of other crawler teams followed across a run of floors.

    Open a campaign file and teams still alive carry onto each floor generated
    after it, keeping their name, origin and standing. Standing is the part the
    party changes in play -- befriend the group that attacked them on floor 2 --
    so it is editable here, and takes effect on the next floor generated."""

    COLUMNS = (("name", "Team", 210), ("standing", "Standing", 85),
               ("party", "Crawlers", 60), ("met", "Met", 40),
               ("seen", "Last seen", 65), ("fate", "Last report", 250))

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=PAD, pady=(PAD, 0))
        ttk.Button(bar, text="Open campaign...", command=self.open).pack(side="left")
        ttk.Button(bar, text="New campaign...", command=self.new).pack(side="left", padx=(4, 0))
        self.close_btn = ttk.Button(bar, text="Close", command=self.close, state="disabled")
        self.close_btn.pack(side="left", padx=(4, 0))
        self.path_lbl = ttk.Label(bar, text="No campaign open", foreground="#666")
        self.path_lbl.pack(side="left", padx=(PAD, 0))

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="browse")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=(key == "fate"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("dead", foreground="#999")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())

        edit = ttk.Labelframe(self, text="Where they stand with the party", padding=PAD)
        edit.pack(fill="x", padx=PAD, pady=(0, PAD))
        self.sel_lbl = ttk.Label(edit, text="Select a team above.", foreground="#666",
                                 justify="left")
        self.sel_lbl.pack(anchor="w")
        row = ttk.Frame(edit)
        row.pack(fill="x", pady=(6, 0))
        self.standing = tk.StringVar()
        # Not readonly: a table can decide a group is "owed a favour" or "blood
        # feud", and the generator takes any wording -- the listed five are just
        # the ones that come with reaction text.
        self.standing_cb = ttk.Combobox(row, textvariable=self.standing,
                                        values=mg.disposition_labels(), state="disabled")
        self.standing_cb.pack(side="left", fill="x", expand=True)
        self.apply_btn = ttk.Button(row, text="Set", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left", padx=(4, 0))
        self.standing_cb.bind("<Return>", lambda e: self.apply())
        ttk.Label(edit, text="Takes effect on the next floor you generate.",
                  foreground="#666", font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        self.refresh()

    # -- campaign file ------------------------------------------------------

    def open(self):
        path = filedialog.askopenfilename(
            parent=self, title="Open a campaign roster",
            filetypes=[("Campaign roster", "*.json"), ("All files", "*.*")])
        if path:
            self._load(path)

    def new(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Start a campaign roster", defaultextension=".json",
            initialfile="campaign.json",
            filetypes=[("Campaign roster", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            mg.save_roster(mg.CampaignRoster(), path)
        except mg.MapgenError as e:
            messagebox.showerror("Could not start a campaign", str(e), parent=self)
            return
        self._load(path)

    def _load(self, path):
        try:
            roster = mg.load_roster(path)
        except mg.MapgenError as e:
            messagebox.showerror("Could not open that campaign", str(e), parent=self)
            return
        self.app.roster = roster
        self.app.campaign_path = path
        self.refresh()
        alive = len(roster.active())
        self.app._set_status(f"Campaign {os.path.basename(path)} - {alive} team(s) alive "
                             f"of {len(roster.teams)} met")

    def close(self):
        """Stop carrying teams forward. The file is left exactly as it is."""
        self.app.roster = None
        self.app.campaign_path = None
        self.refresh()
        self.app._set_status("Campaign closed; floors generate standalone again")

    # -- roster table -------------------------------------------------------

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        path = self.app.campaign_path
        roster = self.app.roster
        self.close_btn.configure(state="normal" if path else "disabled")
        self.path_lbl.configure(
            text=os.path.basename(path) if path else "No campaign open",
            foreground="#222" if path else "#666")
        if not roster or not roster.teams:
            self._select(None)
            return
        for t in roster.teams:
            fate = t.status or "not yet reported"
            seen = f"floor {t.floor}" if t.history else "-"
            if not t.alive:
                fate = f"died on floor {t.died_on} - {fate}"
            # A dash until floor 3, where the System has everyone pick a race
            # and a class and there is finally a party to count.
            party = str(len(t.members)) if t.members else "-"
            self.tree.insert("", "end", iid=t.name,
                             values=(t.name, t.disposition, party, t.first_seen,
                                     seen, fate),
                             tags=() if t.alive else ("dead",))
        self._select(None)

    def _team(self, name):
        roster = self.app.roster
        return next((t for t in roster.teams if t.name == name), None) if roster else None

    def _on_select(self):
        sel = self.tree.selection()
        self._select(self._team(sel[0]) if sel else None)

    def _select(self, team):
        self._selected = team
        if team is None:
            self.sel_lbl.configure(text="Select a team above.", foreground="#666")
            self.standing.set("")
            self.standing_cb.configure(state="disabled")
            self.apply_btn.configure(state="disabled")
            return
        note = "" if team.alive else f"  (died on floor {team.died_on})"
        who = "\n" + mg.party_line(team) if team.members else ""
        self.sel_lbl.configure(text=f"{team.name} - formed from {team.origin}{note}{who}",
                               foreground="#222")
        self.standing.set(team.disposition)
        self.standing_cb.configure(state="normal")
        self.apply_btn.configure(state="normal")

    def apply(self):
        team = getattr(self, "_selected", None)
        standing = self.standing.get().strip()
        if not team or not standing:
            return
        team.disposition = standing
        if self.app.campaign_path:
            try:
                mg.save_roster(self.app.roster, self.app.campaign_path)
            except mg.MapgenError as e:
                messagebox.showerror("Could not save the campaign", str(e), parent=self)
                return
        self.refresh()
        self.tree.selection_set(team.name)
        known = standing in mg.disposition_labels()
        self.app._set_status(f"{team.name} now stands {standing} with the party"
                             + ("" if known else " (no reaction text for that one)"))


class LootView(ttk.Frame):
    """Open loot boxes on demand, apart from any floor.

    A floor names its boxes -- "Drops: a Silver Boss Box" -- as suggestions. Pick
    one from the list to start from, change its tier or type if the table wants
    something else, then open it. Nothing here touches the floor: rolling boxes
    never changes a map. One roller serves the whole app session, so boxes opened
    one after another work through each bank before repeating an item."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.roller = mg.LootRoller()
        self.suggestions = []

        sugg = ttk.Labelframe(self, text="Boxes this floor names - pick one to start from",
                              padding=PAD)
        sugg.pack(fill="x", padx=PAD, pady=(PAD, 0))
        self.sugg_list = tk.Listbox(sugg, height=8, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(sugg, orient="vertical", command=self.sugg_list.yview)
        self.sugg_list.configure(yscrollcommand=sb.set)
        self.sugg_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.sugg_list.bind("<<ListboxSelect>>", lambda e: self._pick())
        self.sugg_list.insert("end", "Generate a floor to list the boxes it names.")

        row = ttk.Frame(self)
        row.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Label(row, text="Tier").pack(side="left")
        self.tier = tk.StringVar(value="Silver")
        self.tier_cb = ttk.Combobox(row, textvariable=self.tier, values=mg.LOOT_BOX_TIERS,
                                    state="readonly", width=10)
        self.tier_cb.pack(side="left", padx=(4, PAD))
        ttk.Label(row, text="Type").pack(side="left")
        self.box_type = tk.StringVar(value="Boss")
        names = [t["name"] for t in mg.LOOT_BOXES["types"] + mg.LOOT_BOXES["themed_types"]]
        # Not readonly: achievements name their own boxes, so any name opens --
        # an unknown one draws from the Adventurer pools.
        self.type_cb = ttk.Combobox(row, textvariable=self.box_type, values=names, width=16)
        self.type_cb.pack(side="left", padx=(4, PAD))
        self.type_cb.bind("<Return>", lambda e: self.open_boxes())
        # The floor sets the gold: none on Floor 1, then (1-10) x floor ^ tier.
        # Filled in from whichever floor was generated last, and editable.
        ttk.Label(row, text="Floor").pack(side="left")
        self.floor = tk.StringVar(value="1")
        ttk.Spinbox(row, from_=1, to=18, textvariable=self.floor, width=4).pack(
            side="left", padx=(4, PAD))
        ttk.Label(row, text="How many").pack(side="left")
        self.count = tk.StringVar(value="1")
        ttk.Spinbox(row, from_=1, to=20, textvariable=self.count, width=4).pack(
            side="left", padx=(4, PAD))
        self.open_btn = ttk.Button(row, text="Open", command=self.open_boxes)
        self.open_btn.pack(side="left")
        ttk.Button(row, text="Clear", command=self.clear).pack(side="right")
        ttk.Button(row, text="Copy", command=self.copy).pack(side="right", padx=(0, 4))

        # Two boxes take one more thing. A sponsor can see what is coming and
        # prepare a Benefactor box for it; a Fan box is whatever the audience
        # decided, and how much they liked it sets the odds.
        extra = ttk.Frame(self)
        extra.pack(fill="x", padx=PAD, pady=(0, PAD))
        ttk.Label(extra, text="Benefactor prepared for").pack(side="left")
        self.NO_THREAT = "nothing in particular"
        self.threat = tk.StringVar(value=self.NO_THREAT)
        ttk.Combobox(extra, textvariable=self.threat, state="readonly", width=18,
                     values=[self.NO_THREAT] + mg.benefactor_threats()).pack(
            side="left", padx=(4, PAD * 2))
        ttk.Label(extra, text="Fan box liked").pack(side="left")
        self.liked = tk.StringVar(value="50")
        ttk.Spinbox(extra, from_=0, to=100, increment=5, textvariable=self.liked,
                    width=5, command=self._show_odds).pack(side="left", padx=(4, 2))
        ttk.Label(extra, text="%").pack(side="left")
        self.odds_lbl = ttk.Label(extra, text="", foreground="#666")
        self.odds_lbl.pack(side="left", padx=(PAD, 0))
        self.liked.trace_add("write", lambda *_: self._show_odds())
        self._show_odds()

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))
        self.out = tk.Text(wrap, wrap="word", font=("Consolas", 10), background="#fbfbfb",
                           relief="flat", padx=10, pady=10, height=10)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.out.yview)
        self.out.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.out.pack(side="left", fill="both", expand=True)
        ttk.Label(self, text=f"Session seed {self.roller.seed}. Newest boxes at the top.",
                  foreground="#666", font=("Segoe UI", 8)).pack(anchor="w", padx=PAD,
                                                                pady=(0, PAD))

    def _show_odds(self):
        """Show what the Fan box's % liked works out to, as it is typed."""
        try:
            odds = mg.fan_odds(float(self.liked.get()))
        except (ValueError, mg.MapgenError):
            self.odds_lbl.configure(text="0 to 100")
            return
        self.odds_lbl.configure(text=f"good {odds['good']:.0%} / neutral "
                                     f"{odds['neutral']:.0%} / bad {odds['bad']:.0%}")

    def load_floor(self, res):
        """List the boxes a freshly generated floor names."""
        self.suggestions = mg.floor_box_suggestions(res.floor)
        self.floor.set(str(res.number))
        self.sugg_list.delete(0, "end")
        if not self.suggestions:
            self.sugg_list.insert("end", "This floor names no boxes. Pick a tier and type below.")
            return
        for sg in self.suggestions:
            self.sugg_list.insert("end", f"{sg.label}   -   {sg.kind}: {sg.source}")

    def _pick(self):
        sel = self.sugg_list.curselection()
        if not sel or sel[0] >= len(self.suggestions):
            return
        sg = self.suggestions[sel[0]]
        self.tier.set(sg.tier)
        self.box_type.set(sg.box_type)

    def open_boxes(self):
        kind = self.box_type.get().strip()
        if not kind:
            messagebox.showerror("Which box?", "Give the box a type, such as Boss or Weapon.",
                                 parent=self)
            return
        try:
            count = max(1, min(20, int(self.count.get())))
        except ValueError:
            count = 1
        try:
            floor = int(self.floor.get())
        except ValueError:
            messagebox.showerror("Which floor?", "The floor sets the gold, so it needs "
                                 "to be a number.", parent=self)
            return
        threat = None if self.threat.get() == self.NO_THREAT else self.threat.get()
        try:
            liked = float(self.liked.get())
        except ValueError:
            liked = 50.0
        try:
            boxes = [self.roller.open(self.tier.get(), kind, floor, threat=threat, liked=liked)
                     for _ in range(count)]
        except mg.MapgenError as e:
            messagebox.showerror("Could not open that box", str(e), parent=self)
            return
        text = "\n".join(b.markdown() for b in boxes)
        self.out.insert("1.0", text + "\n\n")
        self.app._set_status(f"Opened {count} x {boxes[0].label} for Floor {floor}")

    def clear(self):
        self.out.delete("1.0", "end")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.out.get("1.0", "end").strip())
        self.app._set_status("Opened boxes copied to the clipboard")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DCC Mapgen")
        self.geometry("1280x820")
        self.minsize(980, 620)
        self.result = None
        self.roster = None            # the campaign being carried, when one is open
        self.campaign_path = None
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
        self.campaign_tab = CampaignView(right, self)
        right.add(self.campaign_tab, text="Campaign")
        self.loot_tab = LootView(right, self)
        right.add(self.loot_tab, text="Loot Boxes")
        self.notebook = right

        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w",
                  padding=(PAD, 3)).pack(fill="x", side="bottom")

        self._build_controls()
        self._sync_floor()
        self._closing = False
        self._poll_id = self.after(50, self._poll)

    def destroy(self):
        # Stop the queue poller before the widgets go, or its next tick fires
        # against a destroyed interpreter and Tk complains about the command.
        self._closing = True
        if getattr(self, "_poll_id", None):
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        super().destroy()

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
        if not self._closing:
            self._poll_id = self.after(50, self._poll)

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

        f = self._opt_frame("floor9", "Factions")
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
        # The combobox is readonly and filled from FLOOR_LABELS, so a miss should be
        # impossible; falling back beats letting StopIteration out of a Tk callback.
        label = self.floor.get()
        return next((n for n, lbl in FLOOR_LABELS.items() if lbl == label), 1)

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

        kw["roster"] = self.roster
        path = self.campaign_path

        def work():
            try:
                res = mg.generate(**kw)
                if path:
                    mg.save_roster(res.roster, path)
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
        if self.campaign_path:
            self.roster = res.roster       # carry this floor's outcome to the next
        self.campaign_tab.refresh()
        self.loot_tab.load_floor(res)
        carried = sum(1 for t in res.floor.other_crawlers if t.first_seen != t.floor)
        note = f" - {carried} team(s) carried in" if carried else ""
        self._set_status(f"{res.basename()} - {len(res.svgs)} map(s){note}")

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

    # The campaign table, driven straight off a roster rather than through the file
    # dialogs: floor 3 is where crawlers pick a race and a class, so the party
    # column has to fill in by then.
    roster = None
    for f in (1, 2, 3):
        roster = mg.generate(f, "small", 4242, images=False, roster=roster).roster
    app.roster = roster
    app.campaign_tab.refresh()
    app.update()
    rows = app.campaign_tab.tree.get_children()
    check(len(rows) == len(roster.teams), f"campaign table lists every team ({len(rows)})")
    parties = [app.campaign_tab.tree.set(r, "party") for r in rows]
    check(any(p.isdigit() and int(p) > 1 for p in parties),
          f"teams carry a party after floor 3 (column shows {parties})")
    # A team that died on floor 1 or 2 never chose, so pick one that did.
    picked = next(t for t in roster.teams if t.members)
    app.campaign_tab.tree.selection_set(picked.name)
    app.update()
    check(mg.party_line(picked) in app.campaign_tab.sel_lbl.cget("text"),
          "selecting a team names its crawlers")
    app.roster = None
    app.campaign_tab.refresh()

    # The Loot Boxes tab: the floor's own boxes listed, one picked, edited, opened.
    loot = app.loot_tab
    check(len(loot.suggestions) > 10,
          f"loot tab lists the floor's boxes ({len(loot.suggestions)})")
    loot.sugg_list.selection_clear(0, "end")
    loot.sugg_list.selection_set(0)
    loot._pick()
    picked = loot.suggestions[0]
    check(loot.tier.get() == picked.tier and loot.box_type.get() == picked.box_type,
          f"picking a suggestion fills tier and type ({picked.label})")
    loot.tier.set("Gold")                 # the DM's edit, before rolling
    loot.box_type.set("Weapon")
    loot.count.set("3")
    loot.open_boxes()
    app.update()
    opened = loot.out.get("1.0", "end")
    check(opened.count("**Gold Weapon Box**") == 3,
          "three edited boxes opened as Gold Weapon Boxes")
    check(loot.floor.get() == "1" and " gold**" not in opened,
          "a Floor 1 floor fills in Floor 1, and its boxes hold no gold")
    loot.floor.set("6")
    loot.count.set("1")
    loot.open_boxes()
    app.update()
    check(" gold**" in loot.out.get("1.0", "2.0"), "a Floor 6 box holds gold")
    loot.tier.set("Gold")
    loot.box_type.set("Benefactor")
    loot.threat.set("fire")
    loot.open_boxes()
    app.update()
    top_line = loot.out.get("1.0", "2.0")
    check("prepared for fire" in top_line and "From " in top_line,
          "a Benefactor box prepared for fire, with its sponsor's note")
    loot.box_type.set("Fan")
    loot.liked.set("0")
    app.update()
    check("bad 100%" in loot.odds_lbl.cget("text"), "0% liked shows certain bad odds")
    loot.open_boxes()
    app.update()
    check("Bad: " in loot.out.get("1.0", "2.0"), "a Fan box nobody liked comes out bad")

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
