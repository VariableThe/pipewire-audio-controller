#!/usr/bin/env python3
"""Inspect a rendered scene (or any PNG) as text.

Prints a coarse ASCII rendering plus objective pixel statistics so the
layout can be verified without eyeballing a bitmap in a terminal.

    python3 scripts/inspect_scene.py /tmp/scene.png --cols 96
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cairo

RAMP = " .:-=+*#%@"


def load(path: str) -> tuple[int, int, bytes]:
    surface = cairo.ImageSurface.create_from_png(path)
    w, h = surface.get_width(), surface.get_height()
    surface.flush()
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    if stride != w * 4:
        rows = []
        for y in range(h):
            row = data[y * stride : y * stride + w * 4]
            rows.append(row)
        data = b"".join(rows)
    return w, h, data


def lum(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args(argv[1:])

    w, h, data = load(args.image)
    cw, ch = max(1, w // args.cols), max(1, h // args.rows)
    print(f"image: {args.image}  {w}x{h}  sampled {args.cols}x{args.rows} cells of {cw}x{ch}")

    accent = 0
    bright = 0
    for row in range(args.rows):
        line = []
        for col in range(args.cols):
            total = 0.0
            count = 0
            maxc = 0
            for y in range(row * ch, min((row + 1) * ch, h)):
                base = y * w * 4
                for x in range(col * cw, min((col + 1) * cw, w)):
                    i = base + x * 4
                    # cairo ARGB32 is stored BGRA in memory (little endian)
                    b, g, r = data[i], data[i + 1], data[i + 2]
                    total += lum(r, g, b)
                    count += 1
                    maxc = max(maxc, max(r, g, b))
                    if b > r + 30 and b > 90:  # accent-ish blue
                        accent += 1
                    if maxc > 220:
                        bright += 1
            avg = total / max(1, count)
            idx = min(len(RAMP) - 1, int(avg / 256 * len(RAMP)))
            line.append(RAMP[idx])
        print("".join(line))
    if args.stats:
        print(f"\naccent-ish pixels: {accent}")
        print(f"near-white pixels: {bright}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
