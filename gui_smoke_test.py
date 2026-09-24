#!/usr/bin/env python3
"""Headless-ish smoke test for the desktop app, used by CI and handy locally.

Builds the real App, drives a generate through it, and checks that the Markdown,
the map list and the rendered preview all arrived. Exits non-zero on failure.

The app is pumped with update() in a loop instead of mainloop() so the test can
drive it and still hand control back; generation and rasterizing happen on worker
threads, which hand results back through the app's queue, so the pump is what
actually lets those results land.

Run with:  python gui_smoke_test.py
"""

import sys
import time

import dcc_mapgen_gui as g

TIMEOUT = 120


def main() -> int:
    app = g.App()
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
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    print("floor 1 / large / seed 4242")
    app.floor.set(g.FLOOR_LABELS[1])
    app._sync_floor()
    app.size.set("large")
    app.seed.set("4242")
    app.update()
    app.generate()

    check(pump(TIMEOUT, lambda: app.result is not None), "generate finished")
    if app.result is None:
        app.destroy()
        return 1

    res = app.result
    check(res.seed == 4242, f"honored the seed (got {res.seed})")
    check(len(res.svgs) == 17, f"17 maps for a large floor 1 (got {len(res.svgs)})")
    check(len(res.markdown) > 5000, f"markdown written ({len(res.markdown)} chars)")
    check(len(app.text.get("1.0", "end")) > 5000, "markdown reached the Text tab")
    check(app.seed.get() == "4242", "seed box shows the seed used")

    if g.resvg_py is None:
        print("  skip  preview render (resvg-py not installed)")
    else:
        check(pump(TIMEOUT, lambda: app.view._photo is not None), "preview rasterized")
        if app.view._photo is not None:
            check(app.view._photo.width() > 100,
                  f"preview is a real image ({app.view._photo.width()}px wide)")

    # An option combination the generator must reject rather than crash on.
    errs = []
    g.messagebox.showerror = lambda title, msg, **kw: errs.append(msg)
    app.floor.set(g.FLOOR_LABELS[3])
    app._sync_floor()
    app.size.set("continent")
    app.update()
    app.generate()
    pump(30, lambda: bool(errs))
    check(bool(errs), "bad floor/size combination is reported, not crashed on")

    app.destroy()
    print(("FAILED: " + "; ".join(failures)) if failures else "gui ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
