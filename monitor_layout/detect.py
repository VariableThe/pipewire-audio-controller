"""Read-only monitor detection.

Two backends are understood:

* ``hyprctl monitors -j``  (Hyprland; JSON, may include physical size)
* ``wlr-randr``            (generic Wayland; plain text)

Geometry policy (important): one *global* px→cm scale per detection result.
Every panel sits in the compositor's shared arrangement space, so a single
scale keeps side-by-side monitors adjacent and proportional even with mixed
DPI.  The scale is the median px→cm factor of the panels that report a
physical size; if none do, the 96-DPI fallback is used and the whole result
is flagged ``approximate``.  We never convert positions using each display's
own pixel density - that would silently tear mixed-DPI layouts apart.

Parsers are pure functions over captured output; ``detect_monitors`` runs
*read-only* query commands with a short timeout and degrades to a graceful
"manual entry" result when neither tool exists or output cannot be parsed.
"""

from __future__ import annotations

import json
import statistics
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from .model import LayoutError, Monitor

PX_TO_CM_FALLBACK = 2.54 / 96.0  # 96 DPI assumption
COMMAND_TIMEOUT = 5.0
MAX_PANELS = 16


@dataclass
class RawMonitor:
    """Parsed panel in compositor pixels (pre scale-conversion)."""

    name: str
    x_px: float
    y_px: float
    width_px: int  # arrangement-space width (post-rotation)
    height_px: int
    orientation: str
    physical_cm: Optional[tuple[float, float]] = None  # panel (w, h) unrotated
    resolution: str = ""

    def cm_per_px(self) -> Optional[float]:
        """Panel's own px→cm factor in arrangement space (if physical size
        is known).  Rotated panels span their physical height horizontally."""
        if self.physical_cm is None or self.width_px <= 0:
            return None
        horizontal_cm = (
            self.physical_cm[1] if self.orientation == "portrait" else self.physical_cm[0]
        )
        return horizontal_cm / self.width_px


@dataclass
class DetectedMonitor:
    name: str
    x_cm: float
    y_cm: float
    width_cm: float
    height_cm: float
    orientation: str
    physical_known: bool
    resolution: str = ""


@dataclass
class DetectionResult:
    monitors: list[DetectedMonitor] = field(default_factory=list)
    source: str = "none"  # "hyprctl" | "wlr-randr" | "none" | "fixture"
    approximate: bool = False
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def _scale_result(raw: list[RawMonitor], source: str) -> DetectionResult:
    """Apply one consistent global scale to a raw parse."""
    if len(raw) > MAX_PANELS:
        raise LayoutError(f"too many panels reported ({len(raw)} > {MAX_PANELS})")
    if not raw:
        return DetectionResult(monitors=[], source=source, approximate=True)
    scales = [s for m in raw if (s := m.cm_per_px()) is not None]
    approximate = len(scales) != len(raw)
    warnings: list[str] = []
    if scales:
        scale = statistics.median(scales)
        if len(scales) > 1 and max(scales) / min(scales) > 1.1:
            warnings.append(
                "Panels disagree on physical scale by more than 10%; using the "
                "median scale for one consistent workspace geometry (mixed DPI)."
            )
        if approximate:
            warnings.append(
                f"{len(raw) - len(scales)} panel(s) reported no physical size; "
                "their sizes and positions are approximate."
            )
    else:
        scale = PX_TO_CM_FALLBACK
        warnings.append(
            "No panel reported a physical size; geometry uses a 96 DPI "
            "assumption (approximate)."
        )
    monitors = [
        DetectedMonitor(
            name=m.name,
            x_cm=round(m.x_px * scale, 2),
            y_cm=round(m.y_px * scale, 2),
            width_cm=round(m.width_px * scale, 2),
            height_cm=round(m.height_px * scale, 2),
            orientation=m.orientation,
            physical_known=m.physical_cm is not None,
            resolution=m.resolution,
        )
        for m in raw
    ]
    return DetectionResult(
        monitors=monitors, source=source, approximate=approximate, warnings=warnings
    )


# ---------------------------------------------------------------------------
# Pure parsers (compositor-pixel space; scaling happens after)


def parse_hyprctl_monitors(text: str) -> DetectionResult:
    """Parse ``hyprctl monitors -j`` output.  Raises LayoutError on garbage."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LayoutError(f"hyprctl output is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise LayoutError("hyprctl output must be a JSON array")
    raw: list[RawMonitor] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise LayoutError("hyprctl entry is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise LayoutError("hyprctl entry missing monitor name")
        px_w, px_h, x, y = (entry.get(k) for k in ("width", "height", "x", "y"))
        for label, v in (("width", px_w), ("height", px_h), ("x", x), ("y", y)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise LayoutError(f"hyprctl entry {name}: bad {label}")
        if px_w <= 0 or px_h <= 0:
            raise LayoutError(f"hyprctl entry {name}: non-positive resolution")
        transform = entry.get("transform", 0)
        if isinstance(transform, bool) or not isinstance(transform, int):
            raise LayoutError(f"hyprctl entry {name}: bad transform")
        portrait = transform in (1, 3, 5, 7)
        mm_w = entry.get("widthMm")
        mm_h = entry.get("heightMm")
        physical = None
        if (
            isinstance(mm_w, (int, float))
            and isinstance(mm_h, (int, float))
            and not isinstance(mm_w, bool)
            and not isinstance(mm_h, bool)
            and mm_w > 0
            and mm_h > 0
        ):
            physical = (mm_w / 10.0, mm_h / 10.0)
        raw.append(
            RawMonitor(
                name=name,
                x_px=float(x),
                y_px=float(y),
                width_px=int(px_w),
                height_px=int(px_h),
                orientation="portrait" if portrait else "landscape",
                physical_cm=physical,
                resolution=f"{int(px_w)}x{int(px_h)}",
            )
        )
    return _scale_result(raw, "hyprctl")


def parse_wlr_randr(text: str) -> DetectionResult:
    """Parse ``wlr-randr`` human output.

    Example block::

        DP-1 "ACME Display 27 RIGHT"
          Physical size: 597x336 mm2
          Enabled: yes
          Modes:
            2560x1440 px, 59.951000 Hz (preferred, current)
          Position: 2560,0
          Transform: 90
    """
    raw: list[RawMonitor] = []

    def flush(current, mm, disabled):
        if current is not None and not disabled and current.width_px > 0:
            if mm[0] > 0 and mm[1] > 0:
                current.physical_cm = (mm[0] / 10.0, mm[1] / 10.0)
            raw.append(current)

    current: Optional[RawMonitor] = None
    mm = (0, 0)
    disabled = False
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t")):
            flush(current, mm, disabled)
            current, mm, disabled = None, (0, 0), False
            first = line.split(" ", 1)[0].strip().strip('"')
            if first:
                current = RawMonitor(first, 0.0, 0.0, 0, 0, "landscape")
            continue
        if current is None:
            continue
        inner = line.strip()
        if inner == "Enabled: no":
            disabled = True
        elif inner.startswith("Physical size:"):
            size = inner.split(":", 1)[1].strip().split()[0]
            if "x" in size:
                w, _, h = size.partition("x")
                if w.isdigit() and h.isdigit():
                    mm = (int(w), int(h))
        elif "px," in inner.lower() and "hz" in inner.lower() and "current" in inner.lower():
            px = inner.split("px")[0].strip()
            if "x" in px:
                w, _, h = px.partition("x")
                if w.isdigit() and h.isdigit() and int(w) > 0 and int(h) > 0:
                    current.width_px = int(w)
                    current.height_px = int(h)
                    current.resolution = px
        elif inner.startswith("Position:"):
            pos = inner.split(":", 1)[1].strip()
            if "," in pos:
                xs, _, ys = pos.partition(",")
                try:
                    current.x_px = float(xs)
                    current.y_px = float(ys)
                except ValueError as exc:
                    raise LayoutError(f"wlr-randr: bad position {pos!r}") from exc
        elif inner.startswith("Transform:"):
            t = inner.split(":", 1)[1].strip().lower()
            if t in ("90", "270", "flipped-90", "flipped-270"):
                current.orientation = "portrait"
    flush(current, mm, disabled)
    return _scale_result(raw, "wlr-randr")


# ---------------------------------------------------------------------------
# Command runner (read-only)

RunFn = Callable[[list[str]], str]


def _default_runner(argv: list[str]) -> str:
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise LayoutError(
            f"{argv[0]} exited with {proc.returncode}: {proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def detect_monitors(runner: Optional[RunFn] = None) -> DetectionResult:
    """Try the known read-only detection backends in order.

    Never raises: on failure the result carries ``error`` and an empty list
    so the GUI can offer manual entry.
    """
    run = runner or _default_runner
    try:
        return parse_hyprctl_monitors(run(["hyprctl", "monitors", "-j"]))
    except FileNotFoundError:
        pass
    except (LayoutError, subprocess.SubprocessError, OSError):
        pass
    try:
        return parse_wlr_randr(run(["wlr-randr"]))
    except FileNotFoundError:
        pass
    except (LayoutError, subprocess.SubprocessError, OSError):
        pass
    return DetectionResult(
        monitors=[],
        source="none",
        error="Could not detect monitors (hyprctl/wlr-randr unavailable or "
        "unparseable). Add them manually below.",
    )


def detected_to_monitor(dm: DetectedMonitor, monitor_id: str) -> Monitor:
    """Convert a detection entry into an editable :class:`Monitor`."""
    return Monitor(
        id=monitor_id,
        name=dm.name,
        x=round(dm.x_cm, 1),
        y=round(dm.y_cm, 1),
        width=round(dm.width_cm, 1),
        height=round(dm.height_cm, 1),
        orientation=dm.orientation,
    )
