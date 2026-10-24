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

# Which options apply to which floor, what each floor is called, and what each
# map is titled live with the generator, so the web page reads the same ones.
FLOOR_OPTIONS = mg.FLOOR_OPTIONS
FLOOR_LABELS = mg.FLOOR_LABELS
map_title = mg.map_title

PAD = 8

# The maker's tip jar, behind the small button at the right of the status bar.
COFFEE_URL = "https://buymeacoffee.com/MumblesCrzy"


def svg_size(svg: str) -> tuple:
    """Natural pixel size from the SVG root element."""
    w = re.search(r'<svg[^>]*?\bwidth="([\d.]+)', svg)
    h = re.search(r'<svg[^>]*?\bheight="([\d.]+)', svg)
    return (float(w.group(1)) if w else 800.0, float(h.group(1)) if h else 600.0)


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
        # Clicking a part of the map to edit it. The app says where each map
        # draws the floor's parts (none for a floor it can't edit), what to call
        # a part, and what a click on one does.
        self.parts = lambda suffix: []
        self.name_part = lambda path: ""
        self.on_part = lambda path: None
        self.detail = lambda path: None      # the map that shows a part up close
        self._shown = None        # (suffix, zoom) of the picture on the canvas
        self._press = None        # where the button went down, and whether it dragged since
        self._lit = None          # the part lit up on the map

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=PAD, pady=(PAD, 0))
        self.bar = bar
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
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double)
        # The context-menu button: the right one, or on a Mac also Control-click.
        for seq in (("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin"
                    else ("<Button-3>",)):
            self.canvas.bind(seq, self._menu)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self._tip(None))
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

    def refresh(self, svgs):
        """Show maps redrawn after an edit, staying on the same map at the same
        zoom: an edit to one room should not throw the view back to the overview."""
        if not self.maps or len(svgs) != len(self.maps) or self.zoom is None:
            self.load(svgs)
            return
        i = max(self.picker.current(), 0)
        self.maps = list(svgs)
        self.svg = self.maps[i][1]
        self._render()

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

    # A press that doesn't move is a click on whatever part is under it; one
    # that moves more than a few pixels pans instead.
    DRAG = 4

    def _grab(self, event):
        self._press = [event.x, event.y, False]
        self.canvas.scan_mark(event.x, event.y)

    def _pan(self, event):
        p = self._press
        if p and not p[2] and abs(event.x - p[0]) + abs(event.y - p[1]) > self.DRAG:
            p[2] = True
            self._tip(None)
            self.canvas.configure(cursor="fleur")
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _release(self, event):
        p, self._press = self._press, None
        if p and not p[2]:
            part = self._part_at(event)
            if part is not None:
                self.on_part(part.path)
        self._hover(event)

    def _double(self, event):
        """A double-click goes to the map that shows the part up close: a
        neighborhood's rooms. Its first click has already opened it to edit."""
        part = self._part_at(event)
        closer = self._closer(part)
        if closer:
            self._tip(None)
            self.goto(closer)

    def _closer(self, part):
        """The map showing a part up close, if that isn't the map on show."""
        if part is None:
            return None
        suffix = self.detail(part.path)
        return suffix if suffix and suffix != self._suffix() and \
            any(s == suffix for s, _ in self.maps) else None

    def goto(self, suffix):
        """Show one of the floor's maps, by its suffix."""
        # Fitted to the map's size as it is now: the click that came before a
        # double-click may just have opened the edit panel beside it.
        self.update_idletasks()
        for i, (s, _) in enumerate(self.maps):
            if s == suffix:
                self.picker.current(i)
                self._on_pick()
                return

    def menu_items(self, event):
        """What right-clicking the map offers, as (label, action): edit the part
        under the pointer, go to its map up close, or go back to the main map."""
        items = []
        part = self._part_at(event)
        if part is not None:
            name = self.name_part(part.path)
            name = name if len(name) <= 60 else name[:59] + "…"
            items.append((f"Edit {name}", lambda: self.on_part(part.path)))
            closer = self._closer(part)
            if closer:
                items.append((f"Open {map_title(closer)}", lambda: self.goto(closer)))
        if self._suffix() and any(s == "" for s, _ in self.maps):
            items.append(("Back to the main map", lambda: self.goto("")))
        return items

    def _menu(self, event):
        items = self.menu_items(event)
        if not items:
            return
        self._tip(None)
        menu = tk.Menu(self, tearoff=0)
        for label, action in items:
            menu.add_command(label=label, command=action)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # -- the parts of the floor on the map --------------------------------------

    def _suffix(self):
        i = self.picker.current()
        return self.maps[i][0] if 0 <= i < len(self.maps) else None

    def _part_at(self, event):
        """The part of the floor under the pointer, if the picture on show says."""
        if self._shown is None:
            return None
        suffix, zoom = self._shown
        x = self.canvas.canvasx(event.x) / zoom
        y = self.canvas.canvasy(event.y) / zoom
        return mg.hit_at(self.parts(suffix), x, y)

    def _hover(self, event):
        if self._press and self._press[2]:
            return
        part = self._part_at(event)
        self.canvas.configure(cursor="hand2" if part else "")
        if part is None:
            self._tip(None)
            return
        closer = self._closer(part)
        hint = "Click to edit" + (f" · double-click for {map_title(closer)}" if closer else "")
        self._tip((event, f"{self.name_part(part.path)}\n{hint}"))

    def _tip(self, at):
        """A label by the pointer naming the part under it, and what a click does."""
        self.canvas.delete("tip")
        if not at:
            return
        event, words = at
        px, py = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        text = self.canvas.create_text(px + 20, py + 22, anchor="nw", tags="tip",
                                       text=words, font=("Segoe UI", 9), fill="#222")
        # Kept on the screen: to the pointer's left near the right edge, above it
        # near the bottom.
        x0, y0, x1, y1 = self.canvas.bbox(text)
        right = self.canvas.canvasx(0) + self.canvas.winfo_width() - 8
        bottom = self.canvas.canvasy(0) + self.canvas.winfo_height() - 8
        dx = (px - 20 - (x1 - x0)) - x0 if x1 + 6 > right else 0
        dy = (py - 12 - (y1 - y0)) - y0 if y1 + 4 > bottom else 0
        self.canvas.move(text, max(dx, self.canvas.canvasx(0) + 8 - x0) if dx else 0, dy)
        x0, y0, x1, y1 = self.canvas.bbox(text)
        box = self.canvas.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4, tags="tip",
                                           fill="#fffbe6", outline="#b8a35a")
        self.canvas.tag_lower(box, text)

    def light(self, path, see=True):
        """Light up a part of the floor on the map: where it is drawn, or, if
        this map doesn't show it, the part of the map that holds it (a room's
        neighborhood on the overview). None puts the light out."""
        self._lit = tuple(path) if path is not None else None
        self._draw_lit(see)

    def _draw_lit(self, see=False):
        self.canvas.delete("lit")
        if self._lit is None or self._shown is None:
            return
        suffix, zoom = self._shown
        hits = self.parts(suffix)
        holders = [h.path for h in hits if self._lit[:len(h.path)] == h.path]
        if not holders:
            return
        best = max(holders, key=len)
        lit = [h for h in hits if h.path == best]
        for h in lit:
            if h.shape == "circle":
                cx, cy, r = h.box
                box = ((cx - r) * zoom, (cy - r) * zoom, (cx + r) * zoom, (cy + r) * zoom)
                self.canvas.create_oval(*box, outline="#e67e22", width=3, tags="lit")
            else:
                x, y, w, hh = h.box
                box = (x * zoom, y * zoom, (x + w) * zoom, (y + hh) * zoom)
                self.canvas.create_rectangle(*box, outline="#e67e22", width=3, tags="lit")
        self.canvas.tag_raise("tip")
        if see:
            self._see(lit[0], zoom)

    def _see(self, h, zoom):
        """Scroll a lit part into view if it is off the screen."""
        if self._photo is None:
            return
        if h.shape == "circle":
            cx, cy = h.box[0], h.box[1]
        else:
            cx, cy = h.box[0] + h.box[2] / 2, h.box[1] + h.box[3] / 2
        cx, cy = cx * zoom, cy * zoom
        left, top = self.canvas.canvasx(0), self.canvas.canvasy(0)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if left <= cx <= left + cw and top <= cy <= top + ch:
            return
        iw, ih = self._photo.width(), self._photo.height()
        self.canvas.xview_moveto(max(0.0, (cx - cw / 2) / iw))
        self.canvas.yview_moveto(max(0.0, (cy - ch / 2) / ih))

    # -- rendering ----------------------------------------------------------

    def _placeholder(self, msg):
        self._shown = None
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
        svg, zoom, suffix = self.svg, self.zoom, self._suffix()
        self.status.set("Rendering map...")

        def work():
            try:
                png = bytes(resvg_py.svg_to_bytes(svg_string=svg, zoom=zoom))
                data = base64.b64encode(png).decode("ascii")
            except Exception as e:                        # noqa: BLE001 - shown to the user
                self.post(lambda: self._failed(token, e))
                return
            self.post(lambda: self._done(token, data, suffix, zoom))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, token, data, suffix=None, zoom=None):
        if token != self._token:
            return                                        # a newer zoom already won
        self._photo = tk.PhotoImage(data=data)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, self._photo.width(), self._photo.height()))
        self._shown = (suffix, zoom) if zoom else None
        self._draw_lit()
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
    so it is editable here, and takes effect on the next floor generated.

    Not every group the party meets comes out of the generator, so a group met at
    the table can be added here too, and any group's latest report and fate can
    be rewritten -- including killing it, or bringing it back."""

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
        self.add_btn = ttk.Button(bar, text="Add group...", command=self.add_dialog,
                                  state="disabled")
        self.add_btn.pack(side="left", padx=(PAD, 0))
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

        edit = ttk.Labelframe(self, text="Selected group", padding=PAD)
        edit.pack(fill="x", padx=PAD, pady=(0, PAD))
        self.sel_lbl = ttk.Label(edit, text="Select a team above.", foreground="#666",
                                 justify="left")
        self.sel_lbl.pack(anchor="w")
        row = ttk.Frame(edit)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Standing").pack(side="left")
        self.standing = tk.StringVar()
        # Not readonly: a table can decide a group is "owed a favour" or "blood
        # feud", and the generator takes any wording -- the listed five are just
        # the ones that come with reaction text.
        self.standing_cb = ttk.Combobox(row, textvariable=self.standing, width=14,
                                        values=mg.disposition_labels(), state="disabled")
        self.standing_cb.pack(side="left", padx=(4, PAD))
        ttk.Label(row, text="Crawlers").pack(side="left")
        self.crawlers = tk.StringVar()
        # Blank for a group nobody has counted yet: the generator sizes it on floor 3.
        self.crawlers_sb = ttk.Spinbox(row, textvariable=self.crawlers, from_=1, to=20,
                                       width=4, state="disabled")
        self.crawlers_sb.pack(side="left", padx=(4, PAD))
        ttk.Label(row, text="Fate").pack(side="left")
        self.outcome = tk.StringVar()
        # Fatal is the one that kills them; anything else brings a dead group back.
        self.outcome_cb = ttk.Combobox(row, textvariable=self.outcome, width=9,
                                       values=mg.team_outcomes(), state="disabled")
        self.outcome_cb.pack(side="left", padx=(4, PAD))
        self.apply_btn = ttk.Button(row, text="Save", command=self.apply, state="disabled")
        self.apply_btn.pack(side="right")
        report = ttk.Frame(edit)
        report.pack(fill="x", pady=(4, 0))
        ttk.Label(report, text="Latest report").pack(side="left")
        self.report = tk.StringVar()
        self.report_entry = ttk.Entry(report, textvariable=self.report, state="disabled")
        self.report_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        for widget in (self.standing_cb, self.crawlers_sb, self.report_entry):
            widget.bind("<Return>", lambda e: self.apply())
        ttk.Label(edit, text="All of it carries onto the next floor you generate; that "
                             "floor deals every living group a fresh report. Fewer "
                             "crawlers drops the most recent to join.",
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
        # The leaderboard lives in the same campaign, and changes to a group can
        # take a crawler off it, so it is redrawn along with this table. Not yet
        # built while this tab's own constructor runs.
        board_tab = getattr(self.app, "board_tab", None)
        if board_tab is not None:
            board_tab.refresh()
        self.tree.delete(*self.tree.get_children())
        path = self.app.campaign_path
        roster = self.app.roster
        self.close_btn.configure(state="normal" if path else "disabled")
        self.add_btn.configure(state="normal" if roster is not None else "disabled")
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
            # and a class and there is finally a party to count -- unless the DM
            # counted them first.
            party = str(t.crawler_count) if t.crawler_count else "-"
            shown = f"{t.name} (added)" if t.added else t.name
            self.tree.insert("", "end", iid=t.name,
                             values=(shown, t.disposition, party, t.first_seen,
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
            for var in (self.standing, self.crawlers, self.outcome, self.report):
                var.set("")
            for widget in (self.standing_cb, self.crawlers_sb, self.outcome_cb,
                           self.report_entry, self.apply_btn):
                widget.configure(state="disabled")
            return
        note = "" if team.alive else f"  (died on floor {team.died_on})"
        added = "  (added by the DM)" if team.added else ""
        who = "\n" + mg.party_line(team) if team.crawler_count else ""
        self.sel_lbl.configure(
            text=f"{team.name} - formed from {team.origin}{note}{added}{who}",
            foreground="#222")
        self.standing.set(team.disposition)
        self.crawlers.set(str(team.crawler_count or ""))
        self.outcome.set(team.outcome)
        self.report.set(team.status)
        self.standing_cb.configure(state="normal")
        self.crawlers_sb.configure(state="normal")
        self.outcome_cb.configure(state="readonly")
        self.report_entry.configure(state="normal")
        self.apply_btn.configure(state="normal")

    @staticmethod
    def _count(text: str):
        """A crawler count as typed, or None if left blank. Raises ValueError for
        anything that is not a whole number; the engine refuses fewer than one."""
        text = text.strip()
        return int(text) if text else None

    def _bad_count(self):
        messagebox.showerror("How many crawlers?",
                             "Crawlers needs to be a whole number, or blank if nobody "
                             "has counted them.", parent=self)

    def apply(self):
        """Save the selected group's standing, crawler count, fate and latest report."""
        team = getattr(self, "_selected", None)
        standing = self.standing.get().strip()
        if not team or not standing:
            return
        try:
            crawlers = self._count(self.crawlers.get())
        except ValueError:
            self._bad_count()
            return
        was_alive, count_was = team.alive, team.crawler_count
        try:
            mg.set_status(self.app.roster, team.name, status=self.report.get(),
                          outcome=self.outcome.get() or team.outcome, crawlers=crawlers)
        except mg.MapgenError as e:
            messagebox.showerror("Could not change that group", str(e), parent=self)
            return
        team.disposition = standing
        if not self._save():
            return
        self.refresh()
        self.tree.selection_set(team.name)
        if was_alive and not team.alive:
            msg = f"{team.name} is dead, as of floor {team.died_on}"
        elif not was_alive and team.alive:
            msg = f"{team.name} is back, and carries onto the next floor"
        else:
            known = standing in mg.disposition_labels()
            msg = (f"{team.name} now stands {standing} with the party"
                   + ("" if known else " (no reaction text for that one)"))
        n = team.crawler_count
        if n != count_was:
            msg += f"; {n} crawler{'' if n == 1 else 's'}"
        self.app._set_status(msg)

    def _save(self) -> bool:
        if not self.app.campaign_path:
            return True
        try:
            mg.save_roster(self.app.roster, self.app.campaign_path)
        except mg.MapgenError as e:
            messagebox.showerror("Could not save the campaign", str(e), parent=self)
            return False
        return True

    # -- groups met at the table -------------------------------------------------

    def _current_floor(self) -> int:
        """The floor a new group was most likely met on: the deepest one the
        campaign has seen, or the floor on screen if it has seen none."""
        roster = self.app.roster
        if roster and roster.teams:
            return max(t.floor for t in roster.teams)
        return self.app.result.number if self.app.result else 1

    def add_dialog(self):
        """Ask for a group met at the table, then add it."""
        if self.app.roster is None:
            messagebox.showinfo("No campaign open",
                                "Open or start a campaign first; that is where the "
                                "group is kept.", parent=self)
            return
        dlg = tk.Toplevel(self)
        dlg.title("Add a group")
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        body = ttk.Frame(dlg, padding=PAD * 2)
        body.pack(fill="both", expand=True)
        fields = {"name": tk.StringVar(), "origin": tk.StringVar(),
                  "crawlers": tk.StringVar(),
                  "standing": tk.StringVar(value="wary"),
                  "floor": tk.StringVar(value=str(self._current_floor())),
                  "status": tk.StringVar(), "outcome": tk.StringVar(value="steady")}
        rows = (("Name", ttk.Entry(body, textvariable=fields["name"], width=36)),
                ("Formed from", ttk.Entry(body, textvariable=fields["origin"], width=36)),
                ("Crawlers", ttk.Spinbox(body, from_=1, to=20, width=6,
                                         textvariable=fields["crawlers"])),
                ("Standing", ttk.Combobox(body, textvariable=fields["standing"], width=34,
                                          values=mg.disposition_labels())),
                ("Met on floor", ttk.Spinbox(body, from_=1, to=18, width=6,
                                             textvariable=fields["floor"])),
                ("Latest report", ttk.Entry(body, textvariable=fields["status"], width=36)),
                ("Fate", ttk.Combobox(body, textvariable=fields["outcome"], width=34,
                                      values=mg.team_outcomes(), state="readonly")))
        for r, (label, widget) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=2)
            widget.grid(row=r, column=1, sticky="w", padx=(PAD, 0), pady=2)
        ttk.Label(body, text="Formed from reads as \"formed from a hen party on a zeppelin "
                             "tour\". Leave it blank if nobody knows. Leave Crawlers "
                             "blank and the generator sizes the group, as it does any "
                             "other; met on floor 3 or deeper, a count is dealt races "
                             "and classes straight away.",
                  foreground="#666", font=("Segoe UI", 8), wraplength=360,
                  justify="left").grid(row=len(rows), column=0, columnspan=2,
                                       sticky="w", pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(PAD, 0))

        def ok():
            values = {k: v.get() for k, v in fields.items()}
            if self.add_group(values):
                dlg.destroy()

        ttk.Button(buttons, text="Add", command=ok).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="left", padx=(4, 0))
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        rows[0][1].focus_set()
        dlg.grab_set()

    def add_group(self, values: dict) -> bool:
        """Add a group from the dialog's values. Kept apart from the dialog so it
        can be driven without one. Returns whether the group was added."""
        try:
            floor = int(values.get("floor") or self._current_floor())
        except ValueError:
            messagebox.showerror("Which floor?", "Met on floor needs to be a number.",
                                 parent=self)
            return False
        try:
            crawlers = self._count(values.get("crawlers", ""))
        except ValueError:
            self._bad_count()
            return False
        try:
            team = mg.add_team(self.app.roster, values.get("name", ""),
                               origin=values.get("origin", ""),
                               standing=values.get("standing", "wary"), floor=floor,
                               status=values.get("status", ""),
                               outcome=values.get("outcome") or "steady",
                               crawlers=crawlers)
        except mg.MapgenError as e:
            messagebox.showerror("Could not add that group", str(e), parent=self)
            return False
        if not self._save():
            return False
        self.refresh()
        self.tree.selection_set(team.name)
        self.tree.see(team.name)
        self.app._set_status(f"Added {team.name}; it carries onto the next floor you generate")
        return True


class LeaderboardView(ttk.Frame):
    """The Dungeon Leaderboard: the Top Ten by ratings, from Floor 4.

    The first floor of a campaign from 4 down deals it; after that it is the DM's
    to change. Everyone who has ever been on it stays in the table -- off the
    list, or dead -- so a crawler put back on keeps the multiplier they had, and
    the campaign keeps a record of who was ranked when."""

    COLUMNS = (("rank", "Rank", 45), ("name", "Crawler", 160), ("who", "Race, class, group", 240),
               ("mult", "Multiplier", 70), ("bounty", "Bounty", 95),
               ("story", "On and off the list", 300))

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=PAD, pady=(PAD, 0))
        self.add_btn = ttk.Button(bar, text="Add crawler...", command=self.add_dialog,
                                  state="disabled")
        self.add_btn.pack(side="left")
        self.info_lbl = ttk.Label(bar, foreground="#666")
        self.info_lbl.pack(side="left", padx=(PAD, 0))

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="browse")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=(key == "story"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("off", foreground="#777")
        self.tree.tag_configure("dead", foreground="#aaa")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())

        edit = ttk.Labelframe(self, text="Selected crawler", padding=PAD)
        edit.pack(fill="x", padx=PAD, pady=(0, PAD))
        self.sel_lbl = ttk.Label(edit, text="Select a crawler above.", foreground="#666",
                                 justify="left", wraplength=760)
        self.sel_lbl.pack(anchor="w")
        row = ttk.Frame(edit)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Rank").pack(side="left")
        self.rank = tk.StringVar()
        # Blank is off the list: clearing it is how a crawler falls off.
        self.rank_sb = ttk.Spinbox(row, textvariable=self.rank, from_=1, to=mg.BOARD_SIZE,
                                   width=4, state="disabled")
        self.rank_sb.pack(side="left", padx=(4, PAD))
        ttk.Label(row, text="Multiplier").pack(side="left")
        self.mult = tk.StringVar()
        self.mult_sb = ttk.Spinbox(row, textvariable=self.mult, from_=1, to=99, width=4,
                                   state="disabled")
        self.mult_sb.pack(side="left", padx=(4, PAD))
        self.apply_btn = ttk.Button(row, text="Save", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left")
        self.revive_btn = ttk.Button(row, text="Bring back", command=self.revive,
                                     state="disabled")
        self.revive_btn.pack(side="right")
        self.kill_btn = ttk.Button(row, text="Killed...", command=self.kill, state="disabled")
        self.kill_btn.pack(side="right", padx=(0, 4))
        for widget in (self.rank_sb, self.mult_sb):
            widget.bind("<Return>", lambda e: self.apply())
        ttk.Label(edit, text="Clear the rank to take a crawler off the list; give a former "
                             "one a rank to bring them back, multiplier remembered. Everyone "
                             "below a new rank moves down one, and tenth is pushed off. Each "
                             "floor generated adds one to the multiplier of everyone still "
                             "on the list.",
                  foreground="#666", font=("Segoe UI", 8), wraplength=760,
                  justify="left").pack(anchor="w", pady=(4, 0))

        self.refresh()

    # -- table ----------------------------------------------------------------

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        roster = self.app.roster
        floor = mg.board_floor(roster)
        self.add_btn.configure(state="normal" if floor else "disabled")
        if roster is None:
            self.info_lbl.configure(text="Open or start a campaign on the Campaign tab: "
                                         "the leaderboard is kept in it.")
        elif floor is None:
            self.info_lbl.configure(text=f"No leaderboard yet. It starts on Floor "
                                         f"{mg.LEADERBOARD['first_floor']}; generate that "
                                         f"floor or deeper in this campaign.")
        else:
            self.info_lbl.configure(text=f"As of floor {floor}. Changes here count "
                                         f"towards floor {floor}.")
        crawlers = list(roster.leaderboard) if roster else []
        # The list first, then former crawlers, then the dead.
        crawlers.sort(key=lambda c: (0 if c.rank else 1 if c.alive else 2, c.rank or 0, c.name))
        for c in crawlers:
            if c.rank:
                rank, tags, bounty = str(c.rank), (), f"{c.bounty:,}"
            elif c.alive:
                rank, tags, bounty = "-", ("off",), "-"
            else:
                rank, tags, bounty = "dead", ("dead",), "-"
            self.tree.insert("", "end", iid=c.name, tags=tags,
                             values=(rank, c.name + (" (added)" if c.added else ""),
                                     mg.ranked_label(c), f"x{c.multiplier}", bounty,
                                     mg.ranked_story(c)))
        self._select(None)

    def _crawler(self, name):
        roster = self.app.roster
        return next((c for c in roster.leaderboard if c.name == name), None) if roster else None

    def _on_select(self):
        sel = self.tree.selection()
        self._select(self._crawler(sel[0]) if sel else None)

    def _select(self, c):
        self._selected = c
        if c is None:
            self.sel_lbl.configure(text="Select a crawler above.", foreground="#666")
            self.rank.set("")
            self.mult.set("")
            for w in (self.rank_sb, self.mult_sb, self.apply_btn, self.kill_btn,
                      self.revive_btn):
                w.configure(state="disabled")
            return
        who = mg.ranked_label(c)
        status = (f"rank {c.rank}, bounty {c.bounty:,} gold" if c.rank else
                  "off the list" if c.alive else f"died on floor {c.died_on}")
        self.sel_lbl.configure(text=f"{c.name}{' - ' + who if who else ''} ({status})\n"
                                    f"{mg.ranked_story(c)}", foreground="#222")
        self.rank.set(str(c.rank or ""))
        self.mult.set(str(c.multiplier))
        alive = "normal" if c.alive else "disabled"
        for w in (self.rank_sb, self.mult_sb, self.apply_btn, self.kill_btn):
            w.configure(state=alive)
        self.revive_btn.configure(state="disabled" if c.alive else "normal")

    # -- changes ----------------------------------------------------------------

    def _done(self, c, msg):
        """Save, redraw both tabs -- a death here can cost a group a crawler -- and
        keep the crawler selected."""
        if not self.app.campaign_tab._save():
            return
        self.app.campaign_tab.refresh()          # which refreshes this tab too
        if c.name in self.tree.get_children():
            self.tree.selection_set(c.name)
            self.tree.see(c.name)
        self.app._set_status(msg)

    def apply(self):
        """Save the selected crawler's rank and multiplier."""
        c = getattr(self, "_selected", None)
        if c is None:
            return
        try:
            rank = int(self.rank.get()) if self.rank.get().strip() else None
            mult = int(self.mult.get()) if self.mult.get().strip() else c.multiplier
        except ValueError:
            messagebox.showerror("Rank and multiplier", "Rank and multiplier are whole "
                                 "numbers; leave the rank blank for off the list.",
                                 parent=self)
            return
        roster = self.app.roster
        try:
            if rank is None and c.rank is not None:
                mg.drop_from_board(roster, c.name)
            elif rank is not None and rank != c.rank:
                mg.place_on_board(roster, c.name, rank)
            if mult != c.multiplier:
                mg.set_multiplier(roster, c.name, mult)
        except mg.MapgenError as e:
            messagebox.showerror("Could not change the leaderboard", str(e), parent=self)
            self.refresh()
            return
        self._done(c, f"{c.name}: " + (f"rank {c.rank}, x{c.multiplier}, bounty "
                                       f"{c.bounty:,} gold" if c.rank else
                                       f"off the list, x{c.multiplier} remembered"))

    def kill(self):
        c = getattr(self, "_selected", None)
        if c is None:
            return
        claimed = f"Their bounty of {c.bounty:,} gold is claimed" if c.rank else "Nobody claims a bounty"
        group = f", and {c.team} loses them" if c.team else ""
        if not messagebox.askyesno("Killed?", f"Kill {c.name}? {claimed}{group}.",
                                   parent=self):
            return
        try:
            mg.drop_from_board(self.app.roster, c.name, died=True)
        except mg.MapgenError as e:
            messagebox.showerror("Could not change the leaderboard", str(e), parent=self)
            return
        self._done(c, f"{c.name} is dead, on floor {c.died_on}")

    def revive(self):
        c = getattr(self, "_selected", None)
        if c is None:
            return
        try:
            mg.revive_ranked(self.app.roster, c.name)
        except mg.MapgenError as e:
            messagebox.showerror("Could not change the leaderboard", str(e), parent=self)
            return
        self._done(c, f"{c.name} is alive again, off the list until you rank them")

    # -- someone new ------------------------------------------------------------

    def add_dialog(self):
        """Ask for a crawler to put on the list, then add them."""
        roster = self.app.roster
        if mg.board_floor(roster) is None:
            return
        dlg = tk.Toplevel(self)
        dlg.title("Add a crawler to the leaderboard")
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        body = ttk.Frame(dlg, padding=PAD * 2)
        body.pack(fill="both", expand=True)
        groups = [t for t in roster.teams if t.alive and t.members]
        fields = {"name": tk.StringVar(), "team": tk.StringVar(value="(none)"),
                  "race": tk.StringVar(), "class": tk.StringVar(),
                  "rank": tk.StringVar(value=str(mg.BOARD_SIZE)),
                  "multiplier": tk.StringVar(value="1")}
        race_cb = ttk.Combobox(body, textvariable=fields["race"], width=34,
                               values=mg.CRAWLER_RACE)
        class_cb = ttk.Combobox(body, textvariable=fields["class"], width=34,
                                values=mg.CRAWLER_CLASS)
        team_cb = ttk.Combobox(body, textvariable=fields["team"], width=34, state="readonly",
                               values=["(none)"] + [t.name for t in groups])

        def group_picked(_event=None):
            # A crawler from a group is one of its members: offer their races, and
            # take the class from whoever is picked.
            team = next((t for t in groups if t.name == fields["team"].get()), None)
            if team is None:
                race_cb.configure(values=mg.CRAWLER_RACE)
                class_cb.configure(state="normal")
                return
            race_cb.configure(values=[m["race"] for m in team.members])
            fields["race"].set(team.members[0]["race"])
            fields["class"].set(team.members[0]["class"])
            class_cb.configure(state="disabled")

        def race_picked(_event=None):
            team = next((t for t in groups if t.name == fields["team"].get()), None)
            m = team and next((m for m in team.members if m["race"] == fields["race"].get()), None)
            if m:
                fields["class"].set(m["class"])

        team_cb.bind("<<ComboboxSelected>>", group_picked)
        race_cb.bind("<<ComboboxSelected>>", race_picked)
        rows = (("Name", ttk.Entry(body, textvariable=fields["name"], width=36)),
                ("Group", team_cb), ("Race", race_cb), ("Class", class_cb),
                ("Rank", ttk.Spinbox(body, from_=1, to=mg.BOARD_SIZE, width=6,
                                     textvariable=fields["rank"])),
                ("Multiplier", ttk.Spinbox(body, from_=1, to=99, width=6,
                                           textvariable=fields["multiplier"])))
        for r, (label, widget) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=2)
            widget.grid(row=r, column=1, sticky="w", padx=(PAD, 0), pady=2)
        ttk.Label(body, text="Leave Group as (none) for a stranger, or for one of the party. "
                             "A crawler from a group is one of its members, so pick their "
                             "race from it; the class comes with them. Whoever holds the "
                             "rank now, and everyone below, moves down one.",
                  foreground="#666", font=("Segoe UI", 8), wraplength=360,
                  justify="left").grid(row=len(rows), column=0, columnspan=2,
                                       sticky="w", pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(PAD, 0))

        def ok():
            if self.add_crawler({k: v.get() for k, v in fields.items()}):
                dlg.destroy()

        ttk.Button(buttons, text="Add", command=ok).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="left", padx=(4, 0))
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        rows[0][1].focus_set()
        dlg.grab_set()

    def add_crawler(self, values: dict) -> bool:
        """Put a crawler on the list from the dialog's values. Kept apart from the
        dialog so it can be driven without one. Returns whether they were added."""
        try:
            rank = int(values.get("rank") or mg.BOARD_SIZE)
            mult = int(values.get("multiplier") or 1)
        except ValueError:
            messagebox.showerror("Rank and multiplier", "Rank and multiplier are whole "
                                 "numbers.", parent=self)
            return False
        team = values.get("team", "")
        try:
            c = mg.place_on_board(self.app.roster, values.get("name", ""), rank,
                                  race=values.get("race", ""), klass=values.get("class", ""),
                                  team="" if team == "(none)" else team, multiplier=mult)
        except mg.MapgenError as e:
            messagebox.showerror("Could not add that crawler", str(e), parent=self)
            return False
        self._done(c, f"{c.name} is on the list at rank {c.rank}, bounty {c.bounty:,} gold")
        return True


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
        """List what a freshly generated floor suggests: first the threats a
        sponsor would prepare a Benefactor box for, then every box it names."""
        self.suggestions = mg.floor_box_suggestions(res.floor)
        benefactor_from = next(t for t in mg.LOOT_BOXES["types"]
                               if t["name"] == "Benefactor")["from_floor"]
        self.threat_suggestions = (mg.threat_suggestions(mg.floor_body(res.markdown))
                                   if res.number >= benefactor_from else [])
        self.floor.set(str(res.number))
        self.sugg_list.delete(0, "end")
        # One row per suggestion, in the order the list shows them, so a pick can
        # be read straight back.
        self.rows = [("threat", t) for t in self.threat_suggestions]
        self.rows += [("box", sg) for sg in self.suggestions]
        if not self.rows:
            self.sugg_list.insert("end", "This floor names no boxes. Pick a tier and type below.")
            return
        for kind, item in self.rows:
            if kind == "threat":
                self.sugg_list.insert("end", f"Benefactor prepared for {item.label}   -   "
                                             f"{item.count} signs on this floor")
            else:
                self.sugg_list.insert("end", f"{item.label}   -   {item.kind}: {item.source}")

    def _pick(self):
        sel = self.sugg_list.curselection()
        rows = getattr(self, "rows", [])
        if not sel or sel[0] >= len(rows):
            return
        kind, item = rows[sel[0]]
        if kind == "threat":
            # The tier stays the DM's call; the sponsor only knows what is coming.
            self.box_type.set("Benefactor")
            self.threat.set(item.threat)
        else:
            self.tier.set(item.tier)
            self.box_type.set(item.box_type)

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


class EditPanel(ttk.Frame):
    """Edit the floor on show, beside its map.

    The tree is the floor's parts -- neighborhoods and their bosses, mobs and
    rooms; lines and their stations; quadrants and their castles -- read
    straight from the floor's data, so it covers every floor alike. Pick one and
    its values come up in a form; Apply redraws the write-up and every map with
    the change, and Save output keeps it. A few values that hold the floor
    together (a neighborhood's letter, a station's number) are shown but locked.

    Renaming something offers to change the old name wherever else the floor
    has it written, since some sentences were written with names in them when
    the floor was generated. Undo steps back through every change."""

    UNDO_LIMIT = 50

    def __init__(self, master, app):
        super().__init__(master, width=360)
        self.app = app
        self.res = None
        self.undo = []           # snapshots of the floor before each change
        self.paths = {}          # tree item -> path into the floor
        self.items = {}          # path -> tree item
        self.path = None         # the part on show in the form
        self.on_pick = lambda path: None    # told which part is picked, to light it on the map
        self.form = []           # (EditField, read the widget) for it

        head = ttk.Frame(self)
        head.pack(fill="x", padx=PAD, pady=(PAD, 4))
        ttk.Label(head, text="Edit this floor", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.undo_btn = ttk.Button(head, text="Undo", width=7, command=self.undo_last,
                                   state="disabled")
        self.undo_btn.pack(side="right")

        split = ttk.Panedwindow(self, orient="vertical")
        split.pack(fill="both", expand=True, padx=PAD)
        treef = ttk.Frame(split)
        self.tree = ttk.Treeview(treef, show="tree", selectmode="browse")
        tsb = ttk.Scrollbar(treef, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        split.add(treef, weight=1)
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._expand(self.tree.focus()))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._select())

        formf = ttk.Frame(split)
        self.canvas = tk.Canvas(formf, highlightthickness=0, borderwidth=0)
        fsb = ttk.Scrollbar(formf, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=fsb.set)
        fsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        split.add(formf, weight=2)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=PAD, pady=PAD)
        self.apply_btn = ttk.Button(btns, text="Apply", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left")
        self.revert_btn = ttk.Button(btns, text="Revert", command=self._show_form,
                                     state="disabled")
        self.revert_btn.pack(side="left", padx=(4, 0))
        ttk.Label(btns, text="Ctrl+Enter applies", foreground="#777").pack(side="right")
        self._message("Generate or open a floor to edit it.")

    # -- loading ---------------------------------------------------------------

    def load(self, res):
        """A new floor: a fresh tree, nothing to undo."""
        self.res = res
        self.undo.clear()
        self.undo_btn.configure(state="disabled")
        self.path = None
        self.on_pick(None)
        if not mg.is_dataclass(res.floor):
            self.tree.delete(*self.tree.get_children())
            self._message("This is one neighborhood's re-rolled rooms, not a whole floor. "
                          "Generate or open a floor to edit it.")
            return
        self._rebuild()
        self._message("Pick a part of the floor to edit it.")

    def _rebuild(self, opened=(), select=None):
        """Fill the tree again, as it was: the same parts open, the same one
        picked. Names in it may have changed."""
        self.tree.delete(*self.tree.get_children())
        self.paths.clear()
        self.items.clear()
        self._add_children("", ())
        for path in sorted(opened, key=len):
            iid = self.items.get(path)
            if iid:
                self._expand(iid)
                self.tree.item(iid, open=True)
        if select is not None and select in self.items:
            iid = self.items[select]
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _add_children(self, parent, path):
        node = mg.edit_get(self.res.floor, list(path))
        for key, label in mg.edit_children(node):
            child = path + (key,)
            iid = self.tree.insert(parent, "end", text=label)
            self.paths[iid], self.items[child] = child, iid
            if mg.edit_children(mg.edit_get(self.res.floor, list(child))):
                self.tree.insert(iid, "end", text="…", tags=("stub",))

    def _expand(self, iid):
        """Fill in a part's own parts the first time it is opened: a continent
        holds four hundred neighborhoods, and their rooms."""
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and "stub" in self.tree.item(kids[0], "tags"):
            self.tree.delete(kids[0])
            self._add_children(iid, self.paths[iid])

    # -- the form --------------------------------------------------------------

    def _message(self, text):
        for w in self.inner.winfo_children():
            w.destroy()
        self.form = []
        ttk.Label(self.inner, text=text, foreground="#666", wraplength=300,
                  justify="left").pack(anchor="w", padx=4, pady=6)
        self.apply_btn.configure(state="disabled")
        self.revert_btn.configure(state="disabled")

    def _select(self):
        sel = self.tree.selection()
        if sel and sel[0] in self.paths:
            self.path = self.paths[sel[0]]
            self.on_pick(self.path)
            self._show_form()

    def _show_form(self):
        if self.path is None:
            return
        node = mg.edit_get(self.res.floor, list(self.path))
        group = self.path[-2] if len(self.path) >= 2 and isinstance(self.path[-1], int) else None
        fields = mg.edit_fields(node, group)
        if not fields:
            self._message("Nothing to type in here: open it in the tree to see what it holds.")
            return
        for w in self.inner.winfo_children():
            w.destroy()
        self.form = []
        ttk.Label(self.inner, text=mg.edit_label(node, self.path[-1]), wraplength=300,
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=4, pady=(4, 6))
        for f in fields:
            box = ttk.Frame(self.inner)
            box.pack(fill="x", padx=4, pady=(0, 6))
            ttk.Label(box, text=f.label + (" (locked)" if f.locked else ""),
                      foreground="#888" if f.locked else "#333").pack(anchor="w")
            self.form.append((f, *self._widget(box, f)))
        any_open = any(not f.locked for f, _, _ in self.form)
        self.apply_btn.configure(state="normal" if any_open else "disabled")
        self.revert_btn.configure(state="normal" if any_open else "disabled")

    def _widget(self, box, f):
        """A widget for one value, and how to read it back and to fill it in."""
        shown = f.value
        if f.locked:
            text = ", ".join(map(str, shown)) if isinstance(shown, (list, tuple)) else str(shown)
            ttk.Label(box, text=text or "(none)", foreground="#888", wraplength=300).pack(anchor="w")
            return (lambda: f.value), (lambda v: None)
        if f.kind == "bool":
            var = tk.BooleanVar(value=bool(shown))
            ttk.Checkbutton(box, variable=var).pack(anchor="w")
            return var.get, var.set
        if f.kind in ("long", "lines"):
            lines = list(shown) if f.kind == "lines" else [shown or ""]
            rows = min(8, max(3, len(lines) + (1 if f.kind == "lines" else 0)))
            t = tk.Text(box, height=rows, wrap="word", font=("Segoe UI", 9), undo=True)
            t.insert("1.0", "\n".join(lines))
            t.pack(fill="x")
            t.bind("<Control-Return>", lambda e: (self.apply(), "break")[1])

            def fill(value):
                t.delete("1.0", "end")
                t.insert("1.0", value)
            return (lambda: t.get("1.0", "end-1c")), fill
        var = tk.StringVar(value="" if shown is None else
                           (", ".join(map(str, shown)) if f.kind == "list" else str(shown)))
        e = ttk.Entry(box, textvariable=var)
        e.pack(fill="x")
        e.bind("<Control-Return>", lambda ev: (self.apply(), "break")[1])
        return var.get, var.set

    def show(self, path):
        """Pick a part by its path, opening the tree down to it."""
        path = tuple(path)
        for i in range(1, len(path)):
            iid = self.items.get(path[:i])
            if iid:
                self._expand(iid)
                self.tree.item(iid, open=True)
        if path in self.items:
            self.tree.selection_set(self.items[path])
            self.tree.see(self.items[path])
        self.path = path
        self.on_pick(path)
        self._show_form()

    def type_in(self, key, value):
        """Fill in one field of the form, as if typed."""
        for f, _, fill in self.form:
            if f.key == key:
                fill(value)
                return
        raise KeyError(key)

    # -- changing the floor ------------------------------------------------------

    def apply(self):
        if self.path is None or not self.form:
            return
        fl = self.res.floor
        changes = []
        try:
            for f, read, _ in self.form:
                if f.locked:
                    continue
                new = mg.parse_edit(f, read())
                if new != f.value:
                    changes.append((f, new))
        except mg.MapgenError as e:
            messagebox.showerror("Check that value", str(e), parent=self)
            return
        if not changes:
            self.app._set_status("Nothing changed.")
            return

        snapshot = mg.snapshot_floor(fl)
        # Renaming: is the old name written anywhere else on the floor? Asked
        # before anything changes, so Cancel leaves the floor as it was.
        also = []
        typed = [list(self.path) + [f.key] for f, _ in changes]   # left as typed
        for f, new in changes:
            if f.kind in ("text", "long") and isinstance(f.value, str) and len(f.value) >= 3:
                where = [w for w in mg.find_text(fl, f.value) if w not in typed]
                if where and isinstance(new, str):
                    also.append((f.value, new, where))
        renames = []
        for old, new, where in also:
            listed = "\n".join("•  " + mg.describe_path(fl, w)[-110:] for w in where[:6])
            more = f"\n…and {len(where) - 6} more" if len(where) > 6 else ""
            answer = messagebox.askyesnocancel(
                "Rename everywhere?",
                f"“{old}” is also written in {len(where)} other place"
                f"{'s' if len(where) != 1 else ''} on this floor:\n\n{listed}{more}\n\n"
                f"Change it to “{new}” there too?", parent=self)
            if answer is None:
                return
            if answer:
                renames.append((old, new, where))
        for f, new in changes:
            mg.edit_set(fl, list(self.path) + [f.key], new)
        for old, new, where in renames:
            mg.replace_text(fl, old, new, where)
        try:
            res = mg.redraw_floor(self.res)
        except mg.MapgenError as e:
            self._restore(snapshot)
            messagebox.showerror("That change can't be drawn", str(e), parent=self)
            return
        self.undo.append(snapshot)
        del self.undo[:-self.UNDO_LIMIT]
        self.undo_btn.configure(state="normal")
        names = ", ".join(f.label for f, _ in changes)
        extra = sum(len(w) for _, _, w in renames)
        self._done(res, f"Changed {names}" + (f", and {extra} other mention"
                                               f"{'s' if extra != 1 else ''}" if extra else "")
                   + ". Save output (Ctrl+S) keeps it.")

    def undo_last(self):
        if not self.undo:
            return
        self._restore(self.undo.pop())
        if not self.undo:
            self.undo_btn.configure(state="disabled")
        self._done(mg.redraw_floor(self.res), "Undone.")

    def _restore(self, snapshot):
        self.res = mg.replace(self.res, floor=mg.restore_floor(snapshot))

    def _done(self, res, status):
        """Show the redrawn floor everywhere, and the tree as it was."""
        opened = [p for iid, p in self.paths.items() if self.tree.item(iid, "open")]
        self.res = res
        self._rebuild(opened, self.path)
        self._show_form()
        self.app._edited(res, status)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DCC Mapgen")
        self.geometry("1280x820")
        self.minsize(980, 620)
        self.result = None
        self.roster = None            # the campaign being carried, when one is open
        self.campaign_path = None
        self.floor_path = None        # the floor file on show, for Reload
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

        # The Maps tab: the map, and the edit panel to its right when it is open.
        self.maps_pane = ttk.Panedwindow(right, orient="horizontal")
        right.add(self.maps_pane, text="Maps")
        self.view = MapView(self.maps_pane, self.status, self.post, lambda: self._resting)
        self.maps_pane.add(self.view, weight=1)
        self.editor = EditPanel(self.maps_pane, self)
        self.edit_btn = ttk.Button(self.view.bar, text="Edit floor", command=self.toggle_editor)
        self.edit_btn.pack(side="right", padx=(0, PAD))
        # A click on a part of the map opens it in the edit panel, and the part
        # picked in the panel lights up on the map.
        self._parts = (None, {})
        self.view.parts = self._map_parts
        self.view.name_part = self._part_name
        self.view.on_part = self._part_clicked
        self.view.detail = self._part_map
        self.editor.on_pick = self._part_picked

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
        self.board_tab = LeaderboardView(right, self)
        right.add(self.board_tab, text="Leaderboard")
        self.loot_tab = LootView(right, self)
        right.add(self.loot_tab, text="Loot Boxes")
        self.notebook = right

        # The status line, with a small coffee button tucked in at its right end:
        # in Buy Me a Coffee's own yellow, and out of the way of everything else.
        foot = ttk.Frame(self)
        foot.pack(fill="x", side="bottom")
        self.coffee = tk.Button(foot, text="☕ Buy me a coffee", command=self.buy_coffee,
                                font=("Segoe UI", 8, "bold"), bg="#FFDD00", fg="#000000",
                                activebackground="#FFE866", activeforeground="#000000",
                                relief="flat", bd=0, padx=8, pady=1, cursor="hand2")
        self.coffee.pack(side="right", padx=(4, PAD), pady=2)
        ttk.Label(foot, textvariable=self.status, relief="sunken", anchor="w",
                  padding=(PAD, 3)).pack(fill="x", side="left", expand=True)

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

    def buy_coffee(self):
        """Open the maker's Buy Me a Coffee page in the browser."""
        webbrowser.open(COFFEE_URL)

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
        f.add_command(label="Open floor file...", accelerator="Ctrl+O", command=self.open_floor)
        f.add_command(label="Reload floor file", accelerator="Ctrl+R", command=self.reload_floor)
        f.add_separator()
        f.add_command(label="Re-roll rooms from a saved floor...", command=self.reroll_rooms)
        f.add_separator()
        f.add_command(label="Quit", command=self.destroy)
        m.add_cascade(label="File", menu=f)
        e = tk.Menu(m, tearoff=0)
        e.add_command(label="Edit this floor", accelerator="Ctrl+E", command=self.toggle_editor)
        e.add_command(label="Undo edit", accelerator="Ctrl+Z", command=self._undo_edit)
        m.add_cascade(label="Edit", menu=e)
        self.configure(menu=m)
        self.bind("<Control-e>", lambda ev: self.toggle_editor())
        # In a text box Ctrl+Z is the box's own undo; anywhere else, the floor's.
        self.bind("<Control-z>", lambda ev: None if isinstance(ev.widget, (tk.Text, tk.Entry, ttk.Entry))
                  else self._undo_edit())
        self.bind("<Control-g>", lambda e: self.generate())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-o>", lambda e: self.open_floor())
        self.bind("<Control-r>", lambda e: self.reload_floor())

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
        # Both held as percentages, 0-100; _kwargs hands generate() the fraction.
        ttk.Label(f, text="Neighborhood stairwell chance").pack(anchor="w", pady=(6, 0))
        self.stair_chance = tk.DoubleVar(value=100)
        self.stair_scale = self._slider(f, self.stair_chance)
        ttk.Label(f, text="Borough boss rate").pack(anchor="w", pady=(6, 0))
        self.borough_rate = tk.DoubleVar(value=100)
        self.borough_scale = self._slider(f, self.borough_rate)

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
        """A 0-100% slider in whole percents.

        The range is percent rather than the 0-1 the generator takes because Tk
        steps a scale by 1 when its track is clicked or an arrow key pressed: on a
        0-1 scale that is the whole range, so only dragging could reach anything
        between none and all. Here a step is one percent."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 0))
        lbl = ttk.Label(row, text=f"{round(var.get())}%", width=5, anchor="e")

        def moved(value):
            # Dragging lands on fractions of a percent; snap so the label and
            # the floor generated agree.
            pct = round(float(value))
            var.set(pct)
            lbl.configure(text=f"{pct}%")

        scale = ttk.Scale(row, from_=0, to=100, variable=var, orient="horizontal",
                          command=moved)
        scale.pack(side="left", fill="x", expand=True)
        lbl.pack(side="left", padx=(4, 0))
        return scale

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
                      stair_chance=round(self.stair_chance.get()) / 100,
                      borough_boss_rate=round(self.borough_rate.get()) / 100)
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

    # -- the edit panel ---------------------------------------------------------

    def toggle_editor(self):
        """Show the edit panel to the right of the map, or put it away."""
        if self._editing():
            self.maps_pane.forget(self.editor)
            self.edit_btn.configure(text="Edit floor")
            self.view.light(None)
        else:
            self.maps_pane.add(self.editor, weight=0)
            self.edit_btn.configure(text="Close editor")
            self.notebook.select(self.maps_pane)
            self.view.light(self.editor.path, see=False)

    def _editing(self):
        return str(self.editor) in self.maps_pane.panes()

    # -- the map's parts ----------------------------------------------------------

    def _map_parts(self, suffix):
        """Where a map of the floor on show draws each of its parts, worked out
        once per map; none for what the edit panel can't take (re-rolled rooms)."""
        res = self.result
        if res is None or not mg.is_dataclass(res.floor) or suffix is None:
            return []
        if self._parts[0] is not res:
            self._parts = (res, {})
        found = self._parts[1]
        if suffix not in found:
            found[suffix] = mg.map_hits(res.floor, suffix, res.boss_stairs)
        return found[suffix]

    def _part_map(self, path):
        """The map that shows a part up close: a neighborhood's rooms."""
        res = self.result
        if res is None or not mg.is_dataclass(res.floor):
            return None
        return mg.part_map(res.floor, path)

    def _part_name(self, path):
        names = mg.describe_path(self.result.floor, path).split(" › ")
        # A row says little on its own ("1. A1 · A4"): say what it is a row of.
        node = mg.edit_get(self.result.floor, list(path))
        return " › ".join(names[-2:]) if isinstance(node, (list, tuple)) else names[-1]

    def _part_clicked(self, path):
        """A part clicked on the map: open it in the edit panel."""
        if not self._editing():
            self.toggle_editor()
        self.editor.show(path)

    def _part_picked(self, path):
        if self._editing():
            self.view.light(path)

    def _undo_edit(self):
        if self.editor.undo:
            self.editor.undo_last()

    def _edited(self, res, status):
        """A change made in the edit panel: show the redrawn floor everywhere."""
        self.result = res
        top = self.text.yview()[0]
        self.text.delete("1.0", "end")
        self.text.insert("1.0", res.markdown)
        self.text.yview_moveto(top)
        self.view.refresh(res.svgs)
        self.loot_tab.load_floor(res)
        self._set_status(status)

    def _gen_done(self, res):
        self.result = res
        self.floor_path = None
        self.editor.load(res)
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
        # The editable floor file goes alongside, for a whole floor; a re-rolled
        # neighborhood's rooms are not one.
        floor_path = base + mg.FLOOR_FILE_SUFFIX if mg.is_dataclass(res.floor) else None
        # Check the real target names, not just the base: floor 10 writes only
        # _heat1..n and never the bare base name, so checking that alone would have
        # let a re-save clobber a folder without asking.
        targets = [md_path] + [f"{base}{suffix}.{fmt}" for suffix, _ in res.svgs]
        targets += [floor_path] if floor_path else []
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
                if floor_path:
                    mg.save_floor_file(res, floor_path)
            except (mg.MapgenError, OSError) as e:
                msg = str(e)
                self.post(lambda: self._save_failed(msg))
            else:
                n = 1 + len(paths) + (1 if floor_path else 0)
                self.post(lambda: self._save_done(folder, n))

        threading.Thread(target=work, daemon=True).start()

    # -- floor files ------------------------------------------------------------

    def open_floor(self, path=None):
        """Draw a floor file: one Save output wrote, edited by hand or not. It is
        drawn as it stands -- it does not touch the campaign, and Generate still
        makes a fresh floor from the controls."""
        if path is None:
            path = filedialog.askopenfilename(
                parent=self, title="Open a floor file",
                filetypes=[("DCC Mapgen floor", "*" + mg.FLOOR_FILE_SUFFIX),
                           ("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        self.status.set(f"Opening {os.path.basename(path)}...")

        def work():
            try:
                res = mg.open_floor_file(path)
            except mg.MapgenError as e:
                msg = str(e)
                self.post(lambda: self._floor_failed(msg))
            except Exception:                              # noqa: BLE001
                tb = traceback.format_exc()
                self.post(lambda: self._floor_failed(tb))
            else:
                self.post(lambda: self._floor_opened(res, path))

        threading.Thread(target=work, daemon=True).start()

    def reload_floor(self):
        """Draw the open floor file again, after editing it in a text editor."""
        if self.floor_path:
            self.open_floor(self.floor_path)
        else:
            self._set_status("No floor file is open. File > Open floor file... opens one.")

    def _floor_opened(self, res, path):
        self.result = res
        self.floor_path = path
        self.editor.load(res)
        self.save_btn.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", res.markdown)
        self.view.load(res.svgs)
        self.loot_tab.load_floor(res)
        self._set_status(f"{os.path.basename(path)} - floor {res.number}, {res.size}, "
                         f"seed {res.seed} - {len(res.svgs)} map(s). Edit the file and "
                         f"press Ctrl+R to redraw.")

    def _floor_failed(self, msg):
        self._set_status("Could not open the floor file")
        messagebox.showerror("Could not open the floor file", msg, parent=self)

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
        self.floor_path = None
        self.editor.load(res)
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

    # A click on a slider's track, or an arrow key, is what Tk steps by 1. On the
    # old 0-1 sliders that was the whole range: none or all, nothing between.
    check(app._kwargs()["stair_chance"] == 1.0, "stairwell chance starts at 100%")
    app.tk.call("ttk::scale::Increment", str(app.stair_scale), -1)
    app.update()
    stair = app._kwargs()["stair_chance"]
    check(stair == 0.99, f"one click on the stairwell slider is one percent (got {stair})")
    app.borough_scale.set(37.4)              # where a drag can let go
    app.update()
    rate = app._kwargs()["borough_boss_rate"]
    check(rate == 0.37 and app.borough_rate.get() == 37,
          f"a drag settles on a whole percent and maps to its fraction (got {rate})")
    app.stair_chance.set(100)
    app.borough_rate.set(100)

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
    cv = app.campaign_tab
    errors = []
    messagebox.showerror = lambda title, msg, **kw: errors.append(msg)
    added = cv.add_group({"name": "The Sky Pirates", "origin": "a hen party on a zeppelin tour",
                          "crawlers": "4", "standing": "friendly", "floor": "3",
                          "status": "arguing over a map", "outcome": "thriving"})
    app.update()
    check(added and "The Sky Pirates" in cv.tree.get_children(),
          "a group met at the table can be added")
    check(cv.tree.set("The Sky Pirates", "name") == "The Sky Pirates (added)",
          "an added group is marked as added")
    check(cv.tree.set("The Sky Pirates", "party") == "4",
          "an added group shows the crawler count it was given")
    check(not cv.add_group({"name": "the sky pirates"}) and errors,
          "a second group with the same name is refused")
    errors.clear()
    check(not cv.add_group({"name": "The Ghosts", "crawlers": "lots"}) and errors,
          "a crawler count that is not a number is refused")
    cv.add_dialog()
    app.update()
    dialogs = [w for w in cv.winfo_children() if isinstance(w, tk.Toplevel)]
    check(len(dialogs) == 1 and len([w for w in dialogs[0].winfo_children()[0].winfo_children()
                                     if isinstance(w, (ttk.Entry, ttk.Combobox))]) >= 7,
          "the Add group dialog opens with its fields")
    for d in dialogs:
        d.grab_release()
        d.destroy()
    cv.tree.selection_set("The Sky Pirates")
    app.update()
    check(cv.report.get() == "arguing over a map" and cv.outcome.get() == "thriving"
          and cv.crawlers.get() == "4",
          "selecting a group fills its report, fate and crawler count")
    cv.crawlers.set("2")
    cv.apply()
    app.update()
    check(cv.tree.set("The Sky Pirates", "party") == "2"
          and len(mg.find_team(app.roster, "Sky Pirates").members) == 2,
          "a group's crawler count can be edited")
    cv.tree.selection_set("The Sky Pirates")
    app.update()
    cv.outcome.set("fatal")
    cv.report.set("shot down over the market")
    cv.apply()
    app.update()
    sky = mg.find_team(app.roster, "Sky Pirates")
    check(not sky.alive and "dead" in cv.tree.item("The Sky Pirates", "tags"),
          "setting a group's fate to fatal kills it")
    check(sky.status == "shot down over the market", "the report can be rewritten")

    # The Leaderboard tab: nothing before Floor 4, then the Top Ten, edited.
    lb = app.board_tab
    check(not lb.tree.get_children() and str(lb.add_btn.cget("state")) == "disabled",
          "no leaderboard before Floor 4")
    app.roster = mg.generate(4, "small", 4242, images=False, roster=app.roster).roster
    app.campaign_tab.refresh()
    app.update()
    ranked = [r for r in lb.tree.get_children() if lb.tree.set(r, "rank").isdigit()]
    check(len(ranked) == 10, f"Floor 4 deals a Top Ten ({len(ranked)})")
    third = mg.board(app.roster)[2]
    lb.tree.selection_set(third.name)
    app.update()
    lb.rank.set("")
    lb.apply()
    app.update()
    check(third.rank is None and "off" in lb.tree.item(third.name, "tags")
          and "fell off" in lb.tree.set(third.name, "story"),
          "clearing a rank takes a crawler off the list, and the table says so")
    lb.tree.selection_set(third.name)
    app.update()
    lb.rank.set("1")
    lb.mult.set("5")
    lb.apply()
    app.update()
    check(third.rank == 1 and lb.tree.set(third.name, "bounty") == "5,000,000"
          and "came back" in lb.tree.set(third.name, "story"),
          "a former crawler comes back at a rank, and a multiplier can be set")
    errors.clear()
    check(lb.add_crawler({"name": "Hero of the Party", "team": "(none)", "race": "Human",
                          "class": "Bard", "rank": "2", "multiplier": "1"})
          and mg.find_ranked(app.roster, "Hero of the Party").rank == 2,
          "one of the party can be put on the list")
    group = next(t for t in app.roster.active() if t.members)
    check(not lb.add_crawler({"name": "Not In It", "team": group.name, "race": "Nobody",
                              "rank": "3"}) and errors,
          "a crawler from a group has to be one of its members")
    from_group = next((c for c in mg.board(app.roster) if c.team), None)
    if from_group is not None:
        members_were = len(mg.find_team(app.roster, from_group.team).members)
        messagebox.askyesno = lambda *a, **kw: True
        lb.tree.selection_set(from_group.name)
        app.update()
        lb.kill()
        app.update()
        team = mg.find_team(app.roster, from_group.team)
        check(not from_group.alive and "dead" in lb.tree.item(from_group.name, "tags")
              and (len(team.members) == members_were - 1 or not team.alive),
              "killing a ranked crawler costs their group them too")
    lb.add_dialog()
    app.update()
    dialogs = [w for w in lb.winfo_children() if isinstance(w, tk.Toplevel)]
    check(len(dialogs) == 1 and len([w for w in dialogs[0].winfo_children()[0].winfo_children()
                                     if isinstance(w, (ttk.Entry, ttk.Combobox))]) >= 6,
          "the Add crawler dialog opens with its fields")
    for d in dialogs:
        d.grab_release()
        d.destroy()
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
    res6 = mg.generate(6, "medium", 4242, images=False)
    loot.load_floor(res6)
    app.update()
    first = loot.sugg_list.get(0)
    check(first.startswith("Benefactor prepared for "),
          f"a Floor 6 floor leads with what a sponsor would prepare for ({first[:40]})")
    loot.sugg_list.selection_clear(0, "end")
    loot.sugg_list.selection_set(0)
    loot._pick()
    check(loot.box_type.get() == "Benefactor"
          and loot.threat.get() == loot.threat_suggestions[0].threat,
          "picking a threat sets a Benefactor box prepared for it")

    # The coffee button: on screen, and it opens the maker's page. The browser is
    # stood in for, so the test opens nothing.
    opened = []
    real_open = webbrowser.open
    webbrowser.open = lambda url, *a, **kw: opened.append(url)
    try:
        app.coffee.invoke()
    finally:
        webbrowser.open = real_open
    check(app.coffee.winfo_ismapped(), "the coffee button is on screen")
    check(opened == [COFFEE_URL], f"the coffee button opens {COFFEE_URL} (got {opened})")

    # Floor files: Save output writes one beside the Markdown; Open draws it as it
    # was; an edit made in a text editor shows after Reload; a broken one is
    # reported rather than crashed on.
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        generated = app.result
        real_ask = filedialog.askdirectory
        filedialog.askdirectory = lambda **kw: tmp
        try:
            app.save()
            pump(120, lambda: app.status.get().startswith(("Saved", "Save failed")))
        finally:
            filedialog.askdirectory = real_ask
        path = os.path.join(tmp, generated.basename() + mg.FLOOR_FILE_SUFFIX)
        check(os.path.exists(path), "Save output writes the floor file too")
        app.open_floor(path)
        pump(60, lambda: app.floor_path == path)
        check(app.result is not generated and app.result.markdown == generated.markdown
              and app.result.svgs == generated.svgs,
              "a floor file opens to the same write-up and maps")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        old = data["data"]["neighborhoods"][0]["boss"]["name"]
        data["data"]["neighborhoods"][0]["boss"]["name"] = "Mumbles the Unedited"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        before = app.result
        app.reload_floor()
        pump(60, lambda: app.result is not before)
        shown = app.text.get("1.0", "end")
        check("Mumbles the Unedited" in shown and old not in shown,
              "an edit to the floor file shows after Reload")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"format": "dcc-mapgen-floor", ')
        errs_seen = []
        real_err = messagebox.showerror
        messagebox.showerror = lambda title, msg, **kw: errs_seen.append(msg)
        try:
            app.reload_floor()
            pump(30, lambda: bool(errs_seen))
        finally:
            messagebox.showerror = real_err
        check(bool(errs_seen) and "not valid JSON" in errs_seen[0],
              "a broken floor file is reported, not crashed on")

    # The edit panel: a renamed castle reaches the write-up, the map and the tree,
    # and renaming offers to change the name wherever else it is written; a value
    # of the wrong kind is refused and changes nothing; a layout value is locked;
    # Undo puts it all back.
    say("floor 5 / medium / seed 4242, edited")
    app.floor.set(FLOOR_LABELS[5])
    app._sync_floor()
    app.size.set("medium")
    app.seed.set("4242")
    earlier = app.result
    app.generate()
    pump(timeout, lambda: app.result is not earlier)
    original = app.result
    app.toggle_editor()
    app.update()
    ed = app.editor
    check([str(p) for p in app.maps_pane.panes()] == [str(app.view), str(ed)],
          "the edit panel opens to the right of the map")
    castle = ("quadrants", 0, "castle")
    ed.show(castle)
    old_name = original.floor.quadrants[0].castle.name
    asked = []
    real_ask = messagebox.askyesnocancel
    messagebox.askyesnocancel = lambda *a, **kw: asked.append(a) or True
    try:
        ed.type_in("name", "Castle Mumbles")
        ed.apply()
    finally:
        messagebox.askyesnocancel = real_ask
    md = app.result.markdown
    check(bool(asked) and "Castle Mumbles" in md and old_name not in md
          and "Castle Mumbles" in app.result.svgs[0][1],
          "a renamed castle reaches the write-up and the map, everywhere it was written")
    check("Castle Mumbles" in ed.tree.item(ed.items[castle], "text"), "the tree shows the new name")
    ed.show(castle + ("boss",))
    shown, errs_seen = app.result, []
    real_err = messagebox.showerror
    messagebox.showerror = lambda title, msg, **kw: errs_seen.append(msg)
    try:
        ed.type_in("level", "lots")
        ed.apply()
    finally:
        messagebox.showerror = real_err
    check(errs_seen and "whole number" in errs_seen[0] and app.result is shown,
          "a level of the wrong kind is refused and changes nothing")
    ed.show(("quadrants", 0))
    check(any(f.key == "name" and f.locked for f, _, _ in ed.form),
          "a quadrant's name, which the map is laid out by, is locked")
    app._undo_edit()
    app.update()
    check(app.result.markdown == original.markdown, "Undo puts the floor back as it was")
    app.toggle_editor()

    # Clicking the map: a part clicked opens in the edit panel, the pointer names
    # what is under it, a drag pans and picks nothing, and the part picked in the
    # panel lights up on the map -- or the part of the map that holds it.
    say("floor 1 / medium / seed 4242, clicked")
    app.floor.set(FLOOR_LABELS[1])
    app._sync_floor()
    app.size.set("medium")
    app.seed.set("4242")
    earlier = app.result
    app.generate()
    pump(timeout, lambda: app.result is not earlier)
    view, canvas = app.view, app.view.canvas

    def show_map(i, suffix):
        view.picker.current(i)
        view._on_pick()
        pump(timeout, lambda: view._shown is not None and view._shown[0] == suffix)

    def middle(h):
        """Where a part's middle is on the canvas widget, as a click would be."""
        zoom = view._shown[1]
        if h.shape == "circle":
            x, y = h.box[0], h.box[1]
        else:
            x, y = h.box[0] + h.box[2] / 2, h.box[1] + h.box[3] / 2
        return round(x * zoom - canvas.canvasx(0)), round(y * zoom - canvas.canvasy(0))

    def click(h, then=lambda: None):
        x, y = middle(h)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        then()
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        app.update()

    show_map(1, "_room_A")
    rooms = [h for h in app._map_parts("_room_A") if h.shape == "circle" and "rooms" in h.path]
    click(rooms[2])
    want = ("neighborhoods", 0, "rooms", 2)
    check(app._editing() and ed.path == want, f"a room clicked on its map opens in the edit panel {ed.path}")
    pump(1, lambda: bool(canvas.find_withtag("lit")))
    lit = [canvas.coords(i) for i in canvas.find_withtag("lit")]
    z = view._shown[1]
    cx, cy, r = rooms[2].box
    check(len(lit) >= 1 and any(abs(c[0] - (cx - r) * z) < 2 and abs(c[1] - (cy - r) * z) < 2 for c in lit),
          "and lights up on the map")
    x, y = middle(rooms[0])
    # Handled at once: read straight after, with no update for a real pointer
    # over the window to move the tooltip on.
    canvas.event_generate("<Motion>", x=x, y=y)
    tip = " ".join(canvas.itemcget(i, "text") for i in canvas.find_withtag("tip")
                   if canvas.type(i) == "text")
    check("Room A1" in tip and canvas.cget("cursor") == "hand2",
          f"the pointer names the part under it ({tip!r})")
    click(rooms[0], then=lambda: canvas.event_generate("<B1-Motion>", x=x + 40, y=y + 30))
    check(ed.path == want, "a drag pans and picks nothing")

    show_map(0, "")
    boss = next(h for h in app._map_parts("") if h.path == ("neighborhoods", 1, "boss"))
    click(boss)
    check(ed.path == ("neighborhoods", 1, "boss"), "a boss clicked on the overview opens in the edit panel")
    ed.show(("neighborhoods", 2, "rooms", 0))
    app.update()
    cell = next(h for h in app._map_parts("") if h.path == ("neighborhoods", 2))
    lit = [canvas.coords(i) for i in canvas.find_withtag("lit")]
    z = view._shown[1]
    check(any(abs(c[0] - cell.box[0] * z) < 2 and abs(c[1] - cell.box[1] * z) < 2 for c in lit),
          "a room picked in the panel lights its neighborhood on the overview")

    # From a neighborhood on the overview to its rooms: a double-click, or the
    # right-click menu; and back again.
    b_cell = next(h for h in app._map_parts("") if h.path == ("neighborhoods", 1))
    x, y = round((b_cell.box[0] + 115) * z - canvas.canvasx(0)), round((b_cell.box[1] + 14) * z - canvas.canvasy(0))
    canvas.event_generate("<Motion>", x=x, y=y)
    tip = " ".join(canvas.itemcget(i, "text") for i in canvas.find_withtag("tip")
                   if canvas.type(i) == "text")
    check("double-click for Rooms - neighborhood B" in tip, f"pointing at a neighborhood says a double-click goes to its rooms ({tip!r})")
    near = type("Event", (), {"x": x, "y": y})
    labels = [label for label, _ in view.menu_items(near)]
    check(labels[:1] == ["Edit Neighborhood B — The Sunken Sewers"]
          and labels[1:] == ["Open Rooms - neighborhood B"], f"right-clicking it offers both ({labels})")
    for ev in ("<ButtonPress-1>", "<ButtonRelease-1>", "<ButtonPress-1>", "<ButtonRelease-1>"):
        canvas.event_generate(ev, x=x, y=y)
    pump(timeout, lambda: view._shown is not None and view._shown[0] == "_room_B")
    check(view._suffix() == "_room_B" and ed.path == ("neighborhoods", 1),
          "double-clicking it opens it to edit and goes to its room map")
    room = next(h for h in app._map_parts("_room_B") if h.shape == "circle" and "rooms" in h.path)
    rx, ry = middle(room)
    items = view.menu_items(type("Event", (), {"x": rx, "y": ry}))
    check([label for label, _ in items] == ["Edit Room B1", "Back to the main map"],
          f"on a room map, right-clicking offers the way back ({[label for label, _ in items]})")
    items[-1][1]()
    pump(timeout, lambda: view._shown is not None and view._shown[0] == "")
    check(view._suffix() == "", "and it goes back to the main map")
    app.toggle_editor()
    check(not canvas.find_withtag("lit"), "closing the panel puts the light out")

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
