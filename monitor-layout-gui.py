#!/usr/bin/env python3
"""Monitor Layout - a native GTK4/Libadwaita editor for monitor-speaker
arrangements and PipeWire routing.

Usage:
    monitor-layout-gui.py            # live discovery (read-only) + editing
    monitor-layout-gui.py --demo     # bundled fixtures only: no compositor
                                     # queries, no PipeWire queries, no audio
    monitor-layout-gui.py --file FILE.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Monitor layout editor")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="use the bundled fixtures instead of live discovery (no audio, no compositor queries)",
    )
    parser.add_argument("--view", choices=("front", "room"), default="front", help="initial canvas view")
    parser.add_argument("--file", help="load a saved layout profile on startup")
    args = parser.parse_args(argv[1:])

    from monitor_layout.controller import LayoutController
    from monitor_layout.gui import MonitorLayoutApp

    controller = LayoutController()
    if args.demo:
        controller.load_demo(ROOT / "fixtures")
    else:
        controller.discover_sinks()
    if args.file:
        try:
            controller.load(args.file)
        except Exception as exc:  # noqa: BLE001 - user feedback only
            print(f"could not load {args.file}: {exc}", file=sys.stderr)

    try:
        from gi.repository import Gdk
    except Exception as exc:  # noqa: BLE001 - provide static status only
        print(f"Gdk could not be imported: {exc}", file=sys.stderr)
        return 1
    if Gdk.Display.get_default() is None:
        print(
            "No Gdk display is available; set GDK_BACKEND=broadway " "BROADWAY_DISPLAY=:N or run under a graphical session.",
            file=sys.stderr,
        )
        return 1

    app = MonitorLayoutApp(demo=args.demo, controller=controller, initial_view=args.view)
    # Only the program name is handed to GApplication: our own options are
    # parsed above, and GOption would reject them as unknown.
    status = app.run([sys.argv[0]])
    return int(status) if status is not None else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
