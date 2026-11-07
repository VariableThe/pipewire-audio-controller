#!/usr/bin/env python3
"""Render the arrangement canvas offscreen to a PNG.

This paints the *same* scene code the window uses (monitor_layout.canvas),
but into an offscreen Cairo surface: no display server, no compositor, no
PipeWire, no audio.  Used for documentation and for pixel-level checks.

    python3 scripts/render_scene.py --out /tmp/scene.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from monitor_layout.canvas import Palette, Scene  # noqa: E402
from monitor_layout.controller import LayoutController  # noqa: E402


def render(
    controller: LayoutController,
    width: int = 900,
    height: int = 520,
    selected: str | None = None,
    palette: Palette | None = None,
) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    scene = Scene(
        controller.layout,
        numbers=controller.monitor_numbers(),
        selected_id=selected,
        palette=palette or Palette(),
    )
    scene.render(cr, width, height)
    return surface


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/monitor-layout-scene.png")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=520)
    parser.add_argument("--select", default=None)
    args = parser.parse_args(argv[1:])

    controller = LayoutController()
    controller.load_demo(ROOT / "fixtures")
    surface = render(controller, args.width, args.height, args.select)
    surface.write_to_png(args.out)
    print(f"wrote {args.out} ({args.width}x{args.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
