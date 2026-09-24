#!/usr/bin/env python3
"""Smoke test for the desktop app, used by CI and handy locally.

The checks themselves live in dcc_mapgen_gui.selftest() so that the same ones run
against a PyInstaller build via `dcc_mapgen_gui --self-test`; this is just the
convenient way to run them from a source checkout.

Run with:  python gui_smoke_test.py
On a headless Linux box:  xvfb-run -a python gui_smoke_test.py
"""

import sys

from dcc_mapgen_gui import selftest

if __name__ == "__main__":
    sys.exit(selftest())
