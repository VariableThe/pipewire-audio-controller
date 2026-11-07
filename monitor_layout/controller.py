"""Display-free application state and actions.

Everything the GUI can do to a layout lives here, so the behaviour can be
unit-tested without GTK, a display, or any audio device.  The GTK layer
(:mod:`monitor_layout.gui`) only renders this state and forwards user intent
to these methods.

The controller never touches PipeWire state: discovery is read-only and
saving/exporting only writes files the user explicitly asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import detect as detect_mod
from . import audiodev
from .model import (
    DeviceMapping,
    INPUT_LAYOUTS,
    Layout,
    LayoutError,
    Monitor,
    Peripheral,
    ORIENTATIONS,
    SPEAKER_EDGES,
)
from .panning import ContributionResult, compute_contributions
from .profiles import load_profile, save_profile
from .routing import GenerationResult, generate_pipewire_conf

Listener = Callable[[], None]


@dataclass
class DetectionOutcome:
    """What a detection pass produced, for the UI to report honestly."""

    added: int = 0
    source: str = "none"
    approximate: bool = False
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PreviewOutcome:
    """Routing preview plus everything the UI must disclose."""

    contributions: Optional[ContributionResult] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.contributions is not None and not self.errors


class LayoutController:
    """Owns the layout and the actions the GUI performs on it."""

    def __init__(self, layout: Optional[Layout] = None) -> None:
        self.layout = layout if layout is not None else Layout(reference_mode="listener")
        self.sinks: list[audiodev.AudioSink] = []
        self.discovery_error: Optional[str] = None
        self._listeners: list[Listener] = []
        self.demo_fixtures = None

    # -- change notification -------------------------------------------------

    def connect(self, callback: Listener) -> None:
        self._listeners.append(callback)

    def notify(self, layout_changed: bool = True) -> None:
        # layout_changed distinguishes persistent model edits from view-only
        # changes such as canvas selection; listeners may refresh UI either way.
        for cb in list(self._listeners):
            cb(layout_changed)

    # -- monitors ------------------------------------------------------------

    def add_monitor(
        self,
        name: str = "Monitor",
        x: float = 0.0,
        y: float = 0.0,
        width: float = 60.0,
        height: float = 34.0,
        orientation: str = "landscape",
        speaker_count: int = 2,
        speaker_edge: str = "bottom",
    ) -> Monitor:
        mon = Monitor(
            id=self.layout.next_monitor_id(),
            name=name,
            x=x,
            y=y,
            width=width,
            height=height,
            orientation=orientation,
            speaker_count=speaker_count,
            speaker_edge=speaker_edge,
        )
        self.layout.add_monitor(mon)
        self._project_room()
        self.notify()
        return mon

    def remove_monitor(self, monitor_id: str) -> None:
        self.remove_target(monitor_id)
        return

    def rename_monitor(self, monitor_id: str, name: str) -> None:
        mon = self.layout.get(monitor_id)
        if mon is None:
            raise LayoutError(f"unknown monitor {monitor_id!r}")
        mon.name = name.strip() or mon.name
        mon.validate()
        self.notify()

    def set_orientation(self, monitor_id: str, orientation: str) -> None:
        mon = self._require(monitor_id)
        if orientation not in ORIENTATIONS:
            raise LayoutError(f"bad orientation {orientation!r}")
        mon.orientation = orientation
        mon.validate()
        self._project_room()
        self.notify()

    def rotate_monitor(self, monitor_id: str) -> str:
        mon = self._require(monitor_id)
        mon.orientation = "portrait" if mon.orientation == "landscape" else "landscape"
        self._project_room()
        self.notify()
        return mon.orientation

    def set_main(self, monitor_id: str) -> None:
        self.layout.set_main(monitor_id)
        self.notify()

    def set_speaker_count(self, monitor_id: str, count: int) -> None:
        mon = self._require(monitor_id)
        previous_count = mon.speaker_count
        mon.speaker_count = int(count)
        mon.validate()
        # A changed count invalidates a previous confirmation.
        mapping = self.layout.mappings.get(monitor_id)
        if mapping and previous_count != mon.speaker_count:
            mapping.confirmed_speakers = 0
            mapping.channel_order = []
        self.notify()

    def set_speaker_edge(self, monitor_id: str, edge: str) -> None:
        mon = self._require(monitor_id)
        if edge not in SPEAKER_EDGES:
            raise LayoutError(f"bad speaker edge {edge!r}")
        mon.speaker_edge = edge
        self.notify()

    def set_geometry(
        self,
        monitor_id: str,
        *,
        x: Optional[float] = None,
        y: Optional[float] = None,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> None:
        mon = self._require(monitor_id)
        old = (mon.x, mon.y, mon.width, mon.height)
        if x is not None:
            mon.x = float(x)
        if y is not None:
            mon.y = float(y)
        if width is not None:
            mon.width = float(width)
        if height is not None:
            mon.height = float(height)
        try:
            mon.validate()
        except Exception:
            mon.x, mon.y, mon.width, mon.height = old
            raise
        self._project_room()
        self.notify()

    def move_monitor(self, monitor_id: str, x: float, y: float) -> None:
        """Drag-to-reposition; refuses non-finite or absurd coordinates."""
        mon = self._require(monitor_id)
        old_x, old_y = mon.x, mon.y
        mon.x = float(x)
        mon.y = float(y)
        try:
            mon.validate()
        except Exception:
            mon.x, mon.y = old_x, old_y
            raise
        self._project_room()
        self.notify()

    def move_monitor_by(self, monitor_id: str, dx: float, dy: float) -> None:
        mon = self._require(monitor_id)
        self.move_monitor(monitor_id, mon.x + dx, mon.y + dy)

    def reorder(self, monitor_id: str, new_index: int) -> None:
        """Move a monitor within the display order (1-based index)."""
        self._require(monitor_id)
        monitors = self.layout.monitors
        old = next(i for i, m in enumerate(monitors) if m.id == monitor_id)
        new = max(0, min(len(monitors) - 1, new_index))
        if old == new:
            return
        mon = monitors.pop(old)
        monitors.insert(new, mon)
        self.notify()

    def monitor_index(self, monitor_id: str) -> int:
        for i, m in enumerate(self.layout.monitors):
            if m.id == monitor_id:
                return i
        raise LayoutError(f"unknown monitor {monitor_id!r}")

    # -- device mapping ------------------------------------------------------

    def map_device(
        self, monitor_id: str, device: Optional[str], confirmed_speakers: Optional[int] = None, *, speaker_confirmed: bool = False
    ) -> None:
        mon = self._require(monitor_id)
        if device is None:
            self.layout.unmap_device(monitor_id)
            self.notify()
            return
        sink = next((s for s in self.sinks if s.name == device), None)
        if sink is None:
            raise LayoutError("Discover this output before mapping it")
        if sink.is_virtual or sink.kind == "headphones":
            raise LayoutError("Headphones and virtual outputs cannot be mapped as speakers")
        if sink.requires_speaker_confirmation and not speaker_confirmed:
            raise LayoutError("Confirm that this unknown output is a physical speaker first")
        if isinstance(mon, Peripheral):
            mon.speaker_confirmed = True
        count = 0 if confirmed_speakers is None else confirmed_speakers
        self.layout.map_device(monitor_id, device, count)
        self.layout.mappings[monitor_id].speaker_confirmed = bool(speaker_confirmed or sink.auto_speaker_candidate)
        self.notify()

    def unmap_device(self, monitor_id: str) -> None:
        self.layout.unmap_device(monitor_id)
        self.notify()

    def confirm_output_speaker(self, target_id: str) -> None:
        mapping = self.layout.mappings.get(target_id)
        sink = next((s for s in self.sinks if mapping and s.name == mapping.device), None)
        if sink is None or sink.is_virtual or sink.kind == "headphones":
            raise LayoutError("This output is absent or is not a speaker candidate")
        mapping.speaker_confirmed = True
        self.notify()

    def confirm_speaker_count(self, monitor_id: str, count: int) -> None:
        """Explicit user confirmation of the physical speaker count."""
        self._require(monitor_id)
        mapping = self.layout.mappings.get(monitor_id)
        if mapping is None:
            raise LayoutError(f"{monitor_id} has no mapped device to confirm")
        mapping.confirmed_speakers = int(count)
        self.notify()

    def confirm_channel_order(self, monitor_id: str, labels: list[str]) -> None:
        mon = self._require(monitor_id)
        mapping = self.layout.mappings.get(monitor_id)
        if mapping is None or mapping.device is None:
            raise LayoutError("Map an output before confirming channel order")
        candidate = DeviceMapping(mapping.device, mapping.confirmed_speakers, list(labels))
        candidate.validate_channel_order(mon.speaker_count, self.device_channels(mapping.device))
        mapping.channel_order = list(labels)
        self.notify()

    def mapping_for(self, monitor_id: str) -> Optional[DeviceMapping]:
        return self.layout.mappings.get(monitor_id)

    def device_channels(self, device: str) -> Optional[list[str]]:
        for sink in self.sinks:
            if sink.name == device:
                return list(sink.channels)
        return None

    def available_devices(self) -> set[str]:
        return {s.name for s in self.sinks}

    # -- discovery (read-only) ----------------------------------------------

    def discover(self, runner=None) -> DetectionOutcome:
        if self.demo_fixtures is not None:
            return DetectionOutcome(source="fixture", warnings=["Demo keeps the fixture arrangement; live display discovery is disabled."])
        result = detect_mod.detect_monitors(runner)
        if result.error:
            self.discovery_error = result.error
            self.notify()
            return DetectionOutcome(source=result.source, error=result.error)
        added = 0
        existing_by_name = {m.name: m for m in self.layout.monitors}
        for dm in result.monitors:
            existing = existing_by_name.get(dm.name)
            mon = detect_mod.detected_to_monitor(
                dm, existing.id if existing is not None else self.layout.next_monitor_id()
            )
            if existing is not None:
                existing.x = mon.x
                existing.y = mon.y
                existing.width = mon.width
                existing.height = mon.height
                existing.orientation = mon.orientation
                existing.validate()
            else:
                try:
                    self.layout.add_monitor(mon)
                    added += 1
                except LayoutError:
                    continue
        self._project_room()
        self.layout.approximate_scale = result.approximate
        self.discovery_error = None
        self.notify()
        return DetectionOutcome(
            added=added,
            source=result.source,
            approximate=result.approximate,
            warnings=list(result.warnings),
        )

    def discover_sinks(self, runner=None) -> Optional[str]:
        if self.demo_fixtures is not None:
            dump = self.demo_fixtures / "pw-dump-sinks.json"
            self.sinks = audiodev.parse_pw_dump(dump.read_text(encoding="utf-8"))
            self.discovery_error = None
            self.notify(False)
            return None
        result = audiodev.discover_sinks(runner)
        self.sinks = list(result.sinks)
        self.discovery_error = result.error
        self.notify()
        return result.error

    def load_demo(self, fixtures_dir: Path) -> DetectionOutcome:
        """Load the bundled fixtures - no compositor, no PipeWire, no audio."""
        fixtures_dir = Path(fixtures_dir)
        self.demo_fixtures = fixtures_dir
        warnings: list[str] = []
        added = 0
        hypr = fixtures_dir / "hyprctl-dual.json"
        if hypr.exists():
            res = detect_mod.parse_hyprctl_monitors(hypr.read_text(encoding="utf-8"))
            for dm in res.monitors:
                mon = detect_mod.detected_to_monitor(dm, self.layout.next_monitor_id())
                self.layout.add_monitor(mon)
                added += 1
            self.layout.approximate_scale = res.approximate
            warnings.extend(res.warnings)
        dump = fixtures_dir / "pw-dump-sinks.json"
        if dump.exists():
            sinks = audiodev.parse_pw_dump(dump.read_text(encoding="utf-8"))
            self.sinks = sinks
            self.discovery_error = None
        self._project_room()
        self.notify()
        return DetectionOutcome(added=added, source="fixture", warnings=warnings)

    # -- preview / export ----------------------------------------------------

    def preview(self) -> PreviewOutcome:
        outcome = PreviewOutcome()
        if not self.layout.targets():
            outcome.errors.append("Add a display or standalone speaker before previewing.")
            return outcome
        try:
            result = compute_contributions(self.layout)
        except LayoutError as exc:
            outcome.errors.append(str(exc))
            return outcome
        outcome.contributions = result
        outcome.warnings.extend(result.warnings)
        if self.layout.reference_mode == "listener":
            if any(not t.room_confirmed for t in self.layout.targets()):
                outcome.warnings.append("Room positions are approximate projections until you place or confirm each object in Listening area.")
        mapped = self.layout.mapped_device_names()
        if any(s.transport == "bluetooth" and s.name in mapped for s in self.sinks):
            outcome.warnings.append("Bluetooth has variable delay and independent clocks: latency compensation cannot promise acoustic sync or prevent drift. Reconnect, rediscover and re-export after disconnection.")
        return outcome

    def export_conf(self) -> tuple[Optional[str], list[str], list[str]]:
        """Render the PipeWire fragment.  Returns (text, warnings, errors)."""
        outcome = self.preview()
        if not outcome.ok:
            return None, outcome.warnings, outcome.errors
        try:
            for target in self.layout.targets():
                mapping = self.layout.mappings.get(target.id)
                sink = next((s for s in self.sinks if mapping and s.name == mapping.device), None)
                if sink and (sink.is_virtual or sink.kind == "headphones"):
                    raise LayoutError("Mapped output is now classified as headphones or virtual; remove its mapping")
                if sink and sink.requires_speaker_confirmation and not mapping.speaker_confirmed:
                    raise LayoutError("Confirm the unknown mapped output is a physical speaker before export")
            gen: GenerationResult = generate_pipewire_conf(
                self.layout,
                result=outcome.contributions,
                device_channels={
                    s.name: s.channels for s in self.sinks if s.channels
                }
                or None,
                available_devices=self.available_devices(),
            )
        except LayoutError as exc:
            return None, outcome.warnings, outcome.errors + [str(exc)]
        warnings = outcome.warnings + gen.warnings
        return gen.conf_text, warnings, []

    # -- persistence ---------------------------------------------------------

    def save(self, path: Path | str) -> Path:
        path = save_profile(self.layout, path)
        self.notify()
        return path

    def load(self, path: Path | str) -> None:
        layout = load_profile(path)
        self.layout = layout
        self.notify()

    def _project_room(self) -> None:
        self.layout.project_approximate_room()

    def set_listener(self, *, x=None, y=None, facing=None) -> None:
        from copy import deepcopy
        candidate = deepcopy(self.layout)
        for key, value in (("listener_x", x), ("listener_y", y), ("listener_facing", facing)):
            if value is not None:
                setattr(candidate, key, float(value))
        candidate.reference_mode = "listener"
        candidate.listener_confirmed = True
        candidate.validate()
        for key in ("listener_x", "listener_y", "listener_facing", "reference_mode", "listener_confirmed"):
            setattr(self.layout, key, getattr(candidate, key))
        self.notify()

    def set_reference_mode(self, mode: str) -> None:
        if mode not in ("main", "listener"):
            raise LayoutError("Unknown listening reference")
        if mode == "main" and self.layout.main() is None:
            raise LayoutError("Choose a main display before using the legacy reference")
        self.layout.reference_mode = mode
        self._project_room()
        self.notify()

    def clear_main(self) -> None:
        self.layout.reference_mode = "listener"
        for mon in self.layout.monitors:
            mon.is_main = False
        self.notify()

    def set_room_geometry(self, target_id: str, *, x=None, y=None, yaw=None, width=None) -> None:
        from copy import deepcopy
        target = self._require(target_id)
        candidate = deepcopy(target)
        for key, value in (("room_x", x), ("room_y", y), ("room_yaw", yaw)):
            if value is not None:
                setattr(candidate, key, float(value))
        if width is not None:
            candidate.width = float(width)
        candidate.room_confirmed = True
        candidate.validate()
        for key in ("room_x", "room_y", "room_yaw", "room_confirmed", "width"):
            setattr(target, key, getattr(candidate, key))
        self.notify()

    def add_peripheral(self, device: str, *, speaker_confirmed: bool = False) -> Peripheral:
        sink = next((s for s in self.sinks if s.name == device), None)
        if not sink or sink.is_virtual or sink.kind == "headphones":
            raise LayoutError("Choose a detected physical speaker output, not headphones")
        if sink.requires_speaker_confirmation and not speaker_confirmed:
            raise LayoutError("Explicitly confirm this unknown output is a speaker")
        used = {t.id for t in self.layout.targets()}
        index = 1
        while f"speaker-{index}" in used:
            index += 1
        target = Peripheral(id=f"speaker-{index}", name=sink.description,
                            room_x=self.layout.listener_x + 70, room_y=self.layout.listener_y - 70,
                            speaker_count=1, speaker_confirmed=True)
        target.validate()
        self.layout.peripherals.append(target)
        try:
            self.map_device(target.id, device, speaker_confirmed=speaker_confirmed)
        except Exception:
            self.layout.peripherals.remove(target)
            raise
        self.layout.reference_mode = "listener"
        self.notify()
        return target

    def remove_target(self, target_id: str) -> None:
        target = self._require(target_id)
        if isinstance(target, Peripheral):
            self.layout.peripherals.remove(target)
            self.layout.mappings.pop(target_id, None)
        else:
            self.layout.remove_monitor(target_id)
        self._project_room()
        self.notify()

    # -- helpers -------------------------------------------------------------

    def _require(self, monitor_id: str) -> Monitor:
        mon = self.layout.get(monitor_id)
        if mon is None:
            raise LayoutError(f"unknown monitor {monitor_id!r}")
        return mon

    def monitor_numbers(self) -> dict[str, int]:
        """1-based display numbers (the number drawn in the middle)."""
        return {m.id: i + 1 for i, m in enumerate(self.layout.monitors)}
