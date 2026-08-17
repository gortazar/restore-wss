#!/usr/bin/env python3
"""Show the review window against a canned plan, with no daemon and no desktop to restore.

    tools/review-preview.py [--screenshot FILE]

This is how the window is checked and how the screenshot in the README is taken: the real thing
needs a session worth restoring, which is exactly what a test machine does not have.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from restore_wss.review import run_review  # noqa: E402

PLAN = {
    "workspace_count": 3,
    "active_workspace": 0,
    "actions": [
        {
            "index": 0,
            "kind": "launch",
            "description": (
                "start libreoffice-writer.desktop for Thesis "
                "opening file:///home/you/Thesis.odt → workspace 1, eDP-1"
            ),
            "reason": "",
            "confidence": 1.0,
        },
        {
            "index": 1,
            "kind": "terminal",
            "description": (
                "start terminal → workspace 3, eDP-1: claude -r in ~/git/my-repo; ssh my-host in ~"
            ),
            "reason": "",
            "confidence": 1.0,
        },
        {
            "index": 2,
            "kind": "launch",
            "description": "start codium.desktop for my-app — VSCodium → workspace 2",
            "reason": "monitor DP-3 is not connected",
            "confidence": 0.6,
        },
        {
            "index": 3,
            "kind": "place",
            "description": "move  Downloads — Files → workspace 3, eDP-1",
            "reason": "title-similarity 0.94",
            "confidence": 0.94,
        },
    ],
    "skipped": [{"title": "Odd window", "wm_class": "Odd", "reason": "no application id"}],
    "ambiguous": [{"title": "patxi@host: ~", "candidate": "patxi@host: ~/tmp", "score": 0.93}],
    "untouched": [{"title": "Inbox — Mail", "wm_class": "firefox"}],
    "browser": [
        {
            "kind": "open",
            "description": "Reopen 7 tab(s): openvidu-marketing/openvidu.io, OpenSEO and 5 more",
        },
        {
            "kind": "skipped",
            "description": "A browser window on workspace 5: its tabs were never captured",
        },
    ],
    "vpn": [{"name": "work", "kind": "activate", "description": "Reconnect the VPN “work”"}],
}


class PreviewClient:
    def plan_restore(self):
        return PLAN

    def restore(self, only=None):
        print(f"preview: would restore {only}")
        return {"results": []}


if __name__ == "__main__":
    shot = None
    if "--screenshot" in sys.argv:
        shot = sys.argv[sys.argv.index("--screenshot") + 1]
    raise SystemExit(run_review(PreviewClient(), screenshot=shot))
