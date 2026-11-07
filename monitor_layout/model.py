"""Geometry and state model for the monitor-speaker layout.

Monitor x/y are centimeters in a front elevation: +x right and +y down.
Independent room_x/room_y are top-down centimeters with +x right and +y
down; listener facing is clockwise from up (-y). Room yaw rotates the
physical slot row clockwise. Orientation swaps body dimensions, not wiring.
Physical slots run left-to-right along horizontal speaker edges and
top-to-bottom along vertical edges. They never depend on the main monitor.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

ORIENTATIONS = ("landscape", "portrait")
SPEAKER_EDGES = ("bottom", "top", "left", "right")
INPUT_LAYOUTS = ("stereo", "5.1")

MIN_NAME_LEN = 1
MAX_NAME_LEN = 96
MAX_MONITORS = 16
MIN_DIM_CM = 5.0
MAX_DIM_CM = 500.0
MAX_POS_CM = 100000.0
SPEAKER_STANDOFF_CM = 6.0
DEFAULT_VIEWING_DISTANCE_CM = 70.0
MIN_SPEAKERS = 1
MAX_SPEAKERS = 4  # user-confirmed count per monitor; 2 is the common case


class LayoutError(ValueError):
    """Raised for invalid or unsafe layout data."""


# PipeWire node names stay in this charset (alsa names included); mapping
# entry refuses anything else long before routing text is generated.
DEVICE_NAME_OK = re.compile(r"^[A-Za-z0-9_.:\-]+$")
CHANNEL_TOKEN_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutError(f"{name} must be a number, got {type(value).__name__}")
    f = float(value)
    if not math.isfinite(f):
        raise LayoutError(f"{name} must be finite, got {value!r}")
    return f


def _check_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise LayoutError(f"{name} must be text, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise LayoutError(f"{name} must not be empty")
    if len(value) > MAX_NAME_LEN:
        raise LayoutError(f"{name} is too long (max {MAX_NAME_LEN} chars)")
    return value.strip()


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.right and self.y <= py <= self.bottom

    def overlaps(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )


@dataclass
class Monitor:
    """One physical display and the speaker(s) confirmed for it."""

    id: str
    name: str
    x: float = 0.0  # cm, left edge (screen, not including speaker standoff)
    y: float = 0.0  # cm, top edge
    width: float = 62.0  # cm, width in landscape reference orientation
    height: float = 35.0  # cm, height in landscape reference orientation
    orientation: str = "landscape"
    is_main: bool = False
    speaker_count: int = 2
    speaker_edge: str = "bottom"

    room_x: Optional[float] = None  # center, top-down cm; seeded only once
    room_y: float = 0.0
    room_yaw: float = 0.0  # clockwise degrees; 0 means a left-to-right speaker row
    room_confirmed: bool = False

    def __post_init__(self) -> None:
        if self.room_x is None:
            self.room_x = self.rect().center_x

    def room_speaker_points(self) -> list[tuple[float, float]]:
        # Elevation does not become depth. A vertical edge projects to one
        # horizontal position; slots remain distinct despite co-location.
        r = self.rect()
        offsets = [x - r.center_x for x, _ in self.speaker_anchors()]
        a = math.radians(self.room_yaw)
        return [(self.room_x + dx * math.cos(a), self.room_y + dx * math.sin(a))
                for dx in offsets]

    def validate(self) -> None:
        _validate_room(self)
        _check_text(self.id, "monitor id")
        _check_text(self.name, "monitor name")
        x = _finite(self.x, f"{self.id}.x")
        y = _finite(self.y, f"{self.id}.y")
        w = _finite(self.width, f"{self.id}.width")
        h = _finite(self.height, f"{self.id}.height")
        if abs(x) > MAX_POS_CM or abs(y) > MAX_POS_CM:
            raise LayoutError(f"{self.id}: position out of range")
        if not (MIN_DIM_CM <= w <= MAX_DIM_CM) or not (MIN_DIM_CM <= h <= MAX_DIM_CM):
            raise LayoutError(
                f"{self.id}: dimensions must be {MIN_DIM_CM}..{MAX_DIM_CM} cm"
            )
        if self.orientation not in ORIENTATIONS:
            raise LayoutError(f"{self.id}: bad orientation {self.orientation!r}")
        if self.speaker_edge not in SPEAKER_EDGES:
            raise LayoutError(f"{self.id}: bad speaker edge {self.speaker_edge!r}")
        if isinstance(self.speaker_count, bool) or not isinstance(self.speaker_count, int):
            raise LayoutError(f"{self.id}: speaker count must be an integer")
        if not (MIN_SPEAKERS <= self.speaker_count <= MAX_SPEAKERS):
            raise LayoutError(
                f"{self.id}: speaker count must be {MIN_SPEAKERS}..{MAX_SPEAKERS}"
            )
        if not isinstance(self.is_main, bool):
            raise LayoutError(f"{self.id}: is_main must be a boolean")

    def rect(self) -> Rect:
        """Bounding box of the rotated screen body."""
        if self.orientation == "portrait":
            return Rect(self.x, self.y, self.height, self.width)
        return Rect(self.x, self.y, self.width, self.height)

    def speaker_anchors(self) -> list[tuple[float, float]]:
        """Points exactly on the monitor's `speaker_edge` (no standoff) -
        the positions the acoustic model uses."""
        r = self.rect()
        n = self.speaker_count
        points = []
        for k in range(n):
            t = (k + 0.5) / n
            if self.speaker_edge == "bottom":
                points.append((r.x + r.width * t, r.bottom))
            elif self.speaker_edge == "top":
                points.append((r.x + r.width * t, r.y))
            elif self.speaker_edge == "left":
                points.append((r.x, r.y + r.height * t))
            else:  # right
                points.append((r.right, r.y + r.height * t))
        return points

    def speaker_points(self) -> list[tuple[float, float]]:
        """World positions of this monitor's speakers, in on-edge order.

        Points sit just outside `speaker_edge` by SPEAKER_STANDOFF_CM.  The
        order is left-to-right on horizontal edges and top-to-bottom on
        vertical edges, independent of the listening reference.
        """
        r = self.rect()
        n = self.speaker_count
        points = []
        for k in range(n):
            t = (k + 0.5) / n
            if self.speaker_edge == "bottom":
                points.append((r.x + r.width * t, r.bottom + SPEAKER_STANDOFF_CM))
            elif self.speaker_edge == "top":
                points.append((r.x + r.width * t, r.y - SPEAKER_STANDOFF_CM))
            elif self.speaker_edge == "left":
                points.append((r.x - SPEAKER_STANDOFF_CM, r.y + r.height * t))
            else:  # right
                points.append((r.right + SPEAKER_STANDOFF_CM, r.y + r.height * t))
        return points


def _validate_room(target) -> None:
    for name in ("room_x", "room_y"):
        if abs(_finite(getattr(target, name), name)) > MAX_POS_CM:
            raise LayoutError(f"{name} out of range")
    if abs(_finite(target.room_yaw, "room yaw")) > 36000:
        raise LayoutError("room yaw out of range")
    if not isinstance(target.room_confirmed, bool):
        raise LayoutError("room_confirmed must be a boolean")


@dataclass
class Peripheral:
    """A user-identified standalone speaker device, not inferred drivers.

    The physical slots are an explicitly chosen left-to-right row at yaw 0.
    width is its physical slot span, not a distance inferred from audio data.
    """
    id: str
    name: str
    room_x: float = 0.0
    room_y: float = 0.0
    room_yaw: float = 0.0
    width: float = 20.0
    speaker_count: int = 1
    speaker_confirmed: bool = False
    room_confirmed: bool = False

    def validate(self) -> None:
        _check_text(self.id, "speaker id")
        _check_text(self.name, "speaker name")
        _validate_room(self)
        width = _finite(self.width, "speaker width")
        if not MIN_DIM_CM <= width <= MAX_DIM_CM:
            raise LayoutError("speaker width out of range")
        if type(self.speaker_count) is not int or not MIN_SPEAKERS <= self.speaker_count <= MAX_SPEAKERS:
            raise LayoutError("speaker count out of range")
        if not isinstance(self.speaker_confirmed, bool):
            raise LayoutError("speaker_confirmed must be a boolean")

    def room_speaker_points(self) -> list[tuple[float, float]]:
        a = math.radians(self.room_yaw)
        return [(self.room_x + dx * math.cos(a), self.room_y + dx * math.sin(a))
                for dx in (self.width * ((i + 0.5) / self.speaker_count - 0.5)
                           for i in range(self.speaker_count))]


@dataclass
class DeviceMapping:
    """A user-confirmed binding between a monitor and a PipeWire output device.

    `device` is the PipeWire *node.name* (stable across session ids) or None
    for "not connected".  `confirmed_speakers` records the count the user
    verified against physical drivers; it must match the monitor's speaker
    count before routing can be generated.
    """

    device: Optional[str] = None
    confirmed_speakers: int = 0
    channel_order: list[str] = field(default_factory=list)
    speaker_confirmed: bool = False  # explicit unknown-output classification

    def validate_channel_order(self, needed: int, reported: Optional[list[str]]) -> None:
        self.validate()
        if len(self.channel_order) != needed:
            raise LayoutError("Physical channel order is not confirmed for every speaker")
        if not reported:
            raise LayoutError("Discover the sink channel labels before confirming or exporting channel order")
        if len(reported) < needed:
            raise LayoutError(f"sink reports {len(reported)} channel(s), fewer than physical speakers")
        for label in reported:
            if not isinstance(label, str) or not CHANNEL_TOKEN_OK.fullmatch(label):
                raise LayoutError("invalid channel token in sink report")
        if len(set(reported)) != len(reported):
            raise LayoutError("sink reports duplicate channel labels")
        if any(label not in reported for label in self.channel_order):
            raise LayoutError("Physical channel order contains unknown sink labels; re-confirm order")

    def validate(self) -> None:
        if not isinstance(self.speaker_confirmed, bool):
            raise LayoutError("speaker_confirmed must be a boolean")
        if not isinstance(self.channel_order, list) or any(
            not isinstance(ch, str) or not CHANNEL_TOKEN_OK.fullmatch(ch)
            for ch in self.channel_order
        ):
            raise LayoutError("invalid channel order labels")
        if len(set(self.channel_order)) != len(self.channel_order):
            raise LayoutError("duplicate channel order labels")
        if self.device is not None:
            _check_text(self.device, "device name")
        if isinstance(self.confirmed_speakers, bool) or not isinstance(
            self.confirmed_speakers, int
        ):
            raise LayoutError("confirmed_speakers must be an integer")
        if not (0 <= self.confirmed_speakers <= MAX_SPEAKERS):
            raise LayoutError("confirmed_speakers out of range")


@dataclass
class Speaker:
    """A resolved physical speaker point with its viewer-relative role."""

    key: str  # stable, e.g. "monitor-1/L"
    monitor_id: str
    x: float
    y: float
    role: str  # Stable physical slot label, not an automatic calibration
    slot: int = 0  # left-to-right on horizontal edges, top-to-bottom on vertical edges


@dataclass
class Layout:
    name: str = "Monitor layout"
    monitors: list[Monitor] = field(default_factory=list)
    input_layout: str = "stereo"
    mappings: dict[str, DeviceMapping] = field(default_factory=dict)
    viewing_distance_cm: float = DEFAULT_VIEWING_DISTANCE_CM
    approximate_scale: bool = False  # True when geometry came from a px fallback

    peripherals: list[Peripheral] = field(default_factory=list)
    # Legacy API default. New GUI sessions explicitly choose listener mode.
    reference_mode: str = "main"
    listener_x: float = 0.0
    listener_y: float = 70.0
    listener_facing: float = 0.0  # clockwise from room up (-y)
    listener_confirmed: bool = False

    def project_approximate_room(self) -> None:
        """Refresh only unconfirmed positions from front elevation.

        Front x becomes room x, but front y is *never* room depth. This is
        a labeled starting sketch, not measured room geometry. User-confirmed
        positions and seating are never overwritten by monitor detection.
        """
        for mon in self.monitors:
            if not mon.room_confirmed:
                mon.room_x = mon.rect().center_x
                mon.room_y = 0.0
                mon.room_yaw = 0.0
        if not self.listener_confirmed and self.monitors:
            left = min(m.rect().x for m in self.monitors)
            right = max(m.rect().right for m in self.monitors)
            self.listener_x = (left + right) / 2
            self.listener_y = self.viewing_distance_cm
            self.listener_facing = 0.0

    def targets(self) -> list[Monitor | Peripheral]:
        return [*self.monitors, *self.peripherals]

    def add_peripheral(self, peripheral: Peripheral) -> None:
        if len(self.peripherals) >= MAX_MONITORS:
            raise LayoutError("too many standalone speakers")
        if self.get(peripheral.id) is not None:
            raise LayoutError(f"duplicate target id {peripheral.id!r}")
        peripheral.validate()
        self.peripherals.append(peripheral)

    def remove_peripheral(self, peripheral_id: str) -> Peripheral:
        target = self.get(peripheral_id)
        if not isinstance(target, Peripheral):
            raise LayoutError(f"unknown standalone speaker {peripheral_id!r}")
        self.peripherals.remove(target)
        self.mappings.pop(peripheral_id, None)
        return target

    def next_peripheral_id(self) -> str:
        i = 1
        while self.get(f"speaker-{i}") is not None:
            i += 1
        return f"speaker-{i}"

    def clear_main(self) -> None:
        if self.reference_mode != "listener":
            raise LayoutError("Choose listener reference before clearing the main display")
        for mon in self.monitors:
            mon.is_main = False

    # -- mutation -----------------------------------------------------------

    def add_monitor(self, monitor: Monitor) -> None:
        if len(self.monitors) >= MAX_MONITORS:
            raise LayoutError(f"too many monitors (max {MAX_MONITORS})")
        if self.get(monitor.id) is not None:
            raise LayoutError(f"duplicate monitor id {monitor.id!r}")
        monitor.validate()
        self.monitors.append(monitor)
        if monitor.is_main or (self.reference_mode == "main" and self.main() is None):
            self.set_main(monitor.id)

    def remove_monitor(self, monitor_id: str) -> Monitor:
        mon = self.get(monitor_id)
        if not isinstance(mon, Monitor):
            raise LayoutError(f"unknown monitor {monitor_id!r}")
        self.monitors.remove(mon)
        self.mappings.pop(monitor_id, None)
        if mon.is_main and self.monitors and self.reference_mode == "main":
            self.monitors[0].is_main = True
        return mon

    def get(self, monitor_id: str) -> Optional[Monitor | Peripheral]:
        for m in self.targets():
            if m.id == monitor_id:
                return m
        return None

    def set_main(self, monitor_id: str) -> None:
        mon = self.get(monitor_id)
        if not isinstance(mon, Monitor):
            raise LayoutError(f"unknown monitor {monitor_id!r}")
        for m in self.monitors:
            m.is_main = m is mon

    def next_monitor_id(self) -> str:
        used = {m.id for m in self.targets()}
        i = 1
        while f"monitor-{i}" in used:
            i += 1
        return f"monitor-{i}"

    def map_device(
        self, monitor_id: str, device: Optional[str], confirmed_speakers: int
    ) -> None:
        """Assign a device to a monitor; rejects duplicate device mappings."""
        mon = self.get(monitor_id)
        if mon is None:
            raise LayoutError(f"unknown monitor {monitor_id!r}")
        if device is not None:
            device = _check_text(device, "device name")
            if not DEVICE_NAME_OK.fullmatch(device):
                raise LayoutError(
                    f"device name {device!r} contains unsupported characters"
                )
            for other_id, mapping in self.mappings.items():
                if other_id != monitor_id and mapping.device == device:
                    raise LayoutError(
                        f"device {device!r} is already mapped to {other_id!r}"
                    )
        self.mappings[monitor_id] = DeviceMapping(device, confirmed_speakers)

    def unmap_device(self, monitor_id: str) -> None:
        if self.get(monitor_id) is None:
            raise LayoutError(f"unknown monitor {monitor_id!r}")
        self.mappings.pop(monitor_id, None)

    # -- queries ------------------------------------------------------------

    def main(self) -> Optional[Monitor]:
        for m in self.monitors:
            if m.is_main:
                return m
        return None

    def mapped_device_names(self) -> set[str]:
        return {mp.device for mp in self.mappings.values() if mp.device}

    def overlaps(self) -> list[tuple[str, str]]:
        """Pairs of monitor ids whose screen bodies overlap (a warning, not an
        error - small overlaps with bezels are common after rotation)."""
        result = []
        for i, a in enumerate(self.monitors):
            for b in self.monitors[i + 1 :]:
                if a.rect().overlaps(b.rect()):
                    result.append((a.id, b.id))
        return result

    def bounds(self) -> Optional[Rect]:
        if not self.monitors:
            return None
        rects = [m.rect() for m in self.monitors]
        x = min(r.x for r in rects)
        y = min(r.y for r in rects)
        right = max(r.right for r in rects)
        bottom = max(r.bottom for r in rects)
        return Rect(x, y, right - x, bottom - y)

    def listening_axis(self) -> tuple[float, float]:
        """Acoustic reference: (reference_x, viewing_distance_cm).

        The arrangement space is a front-elevation view (like the desktop
        display-settings panel): monitors are arranged facing the user on one
        frontal plane.  Acoustically, only the *horizontal* offset of a
        speaker from the main display's centre matters - its azimuth is
        atan2(offset, viewing_distance).  Vertical stacking is elevation and
        is intentionally ignored for panning; it never implies a speaker
        sits in front of or behind the listener.
        """
        main = self.main()
        if main is None:
            raise LayoutError("no main monitor selected")
        return main.rect().center_x, self.viewing_distance_cm

    def reference_point(self) -> tuple[float, float]:
        """Canvas-visualisation point for the listener, one viewing distance
        out from the main display's bottom edge.  Drawing aid only - the
        acoustic model uses :meth:`listening_axis`."""
        if self.reference_mode == "listener":
            return (self.listener_x, self.listener_y)
        main = self.main()
        if main is None:
            raise LayoutError("no main monitor selected")
        r = main.rect()
        return (r.center_x, r.bottom + self.viewing_distance_cm)

    def _speaker_angle(self, speaker: Speaker) -> float:
        if self.reference_mode == "main":
            ref_x, distance = self.listening_axis()
            return math.degrees(math.atan2(speaker.x - ref_x, distance))
        angle = math.degrees(math.atan2(speaker.x - self.listener_x,
                                        self.listener_y - speaker.y))
        return (angle - self.listener_facing + 180) % 360 - 180

    def speakers(self) -> list[Speaker]:
        """Stable physical slots; sorted by their current listener azimuth."""
        speakers = []
        for target in self.targets():
            if self.reference_mode == "main" and isinstance(target, Monitor):
                points = target.speaker_anchors()
            else:
                points = target.room_speaker_points()
            for slot, (x, y) in enumerate(points):
                count = target.speaker_count
                role = "C" if count == 1 else "L" if slot == 0 else "R" if slot == count - 1 else f"S{slot}"
                speakers.append(Speaker(f"{target.id}/{role}", target.id, x, y, role, slot))
        speakers.sort(key=lambda sp: (self._speaker_angle(sp), sp.monitor_id, sp.slot))
        return speakers

    def speaker_azimuths(self) -> list[float]:
        return [self._speaker_angle(sp) for sp in self.speakers()]

    def speaker_anchors(self) -> list[tuple[float, float]]:
        return [(sp.x, sp.y) for sp in self.speakers()]

    def validate(self) -> None:
        _check_text(self.name, "layout name")
        if len(self.monitors) > MAX_MONITORS:
            raise LayoutError(f"too many monitors (max {MAX_MONITORS})")
        d = _finite(self.viewing_distance_cm, "viewing distance")
        if not (10.0 <= d <= 500.0):
            raise LayoutError("viewing distance out of range (10..500 cm)")
        if self.input_layout not in INPUT_LAYOUTS:
            raise LayoutError(f"bad input layout {self.input_layout!r}")
        if not isinstance(self.listener_confirmed, bool):
            raise LayoutError("listener_confirmed must be a boolean")
        if self.reference_mode not in ("main", "listener"):
            raise LayoutError("unknown listening reference mode")
        for name in ("listener_x", "listener_y"):
            if abs(_finite(getattr(self, name), name)) > MAX_POS_CM:
                raise LayoutError("listener position out of range")
        if abs(_finite(self.listener_facing, "listener facing")) > 36000:
            raise LayoutError("listener facing out of range")
        if len(self.peripherals) > MAX_MONITORS:
            raise LayoutError("too many standalone speakers")
        if self.peripherals and self.reference_mode != "listener":
            raise LayoutError("Standalone speakers require the seated listener reference")
        ids = [m.id for m in self.targets()]
        if len(set(ids)) != len(ids):
            raise LayoutError("duplicate monitor ids")
        mains = [m for m in self.monitors if m.is_main]
        if len(mains) > 1 or (self.reference_mode == "main" and self.monitors and len(mains) != 1):
            raise LayoutError("exactly one main monitor is required")
        for m in self.targets():
            m.validate()
        for mid, mapping in self.mappings.items():
            if self.get(mid) is None:
                raise LayoutError(f"mapping for unknown monitor {mid!r}")
            mapping.validate()
            if mapping.device is not None and not (
                mapping.confirmed_speakers == 0
                or mapping.confirmed_speakers == self.get(mid).speaker_count
            ):
                raise LayoutError(
                    f"{mid}: user confirmed {mapping.confirmed_speakers} speaker(s) "
                    f"but the layout declares {self.get(mid).speaker_count}; "
                    "re-confirm after changing the count"
                )
        devices: dict[str, str] = {}
        for mid, mapping in self.mappings.items():
            if mapping.device is not None:
                if mapping.device in devices:
                    raise LayoutError(
                        f"device {mapping.device!r} mapped to both "
                        f"{devices[mapping.device]!r} and {mid!r}"
                    )
                devices[mapping.device] = mid

    def normalize(self, margin_cm: float = 4.0) -> None:
        """Shift all monitors so the bounding box starts at (margin, margin)."""
        b = self.bounds()
        if b is None:
            return
        dx, dy = margin_cm - b.x, margin_cm - b.y
        for m in self.monitors:
            m.x += dx
            m.y += dy
