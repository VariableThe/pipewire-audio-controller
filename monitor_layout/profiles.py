"""Profile (layout) persistence.

JSON, schema-versioned, validated on load, written atomically (tmp file +
rename).  ``json`` is set to reject NaN/Infinity on both write and parse, so
corrupted or hand-edited profiles fail loudly instead of poisoning geometry.

Saving or loading a profile never touches PipeWire state.
"""

from __future__ import annotations

from dataclasses import asdict

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .model import DeviceMapping, Layout, LayoutError, Monitor, Peripheral

APP_DIR_NAME = "monitor-layout"
PROFILE_SUFFIX = ".json"


def default_profiles_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME / "profiles"


# -- (de)serialisation ------------------------------------------------------


def layout_to_dict(layout: Layout) -> dict[str, Any]:
    layout.validate()
    return {
        "schema": SCHEMA_VERSION,
        "name": layout.name,
        "input_layout": layout.input_layout,
        "viewing_distance_cm": layout.viewing_distance_cm,
        "approximate_scale": layout.approximate_scale,
        "reference_mode": layout.reference_mode,
        "listener_x": layout.listener_x,
        "listener_y": layout.listener_y,
        "listener_facing": layout.listener_facing,
        "listener_confirmed": layout.listener_confirmed,
        "monitors": [asdict(m) for m in layout.monitors],
        "peripherals": [asdict(p) for p in layout.peripherals],
        "mappings": {
            mid: {"device": mp.device, "confirmed_speakers": mp.confirmed_speakers,
                  "channel_order": list(mp.channel_order),
                  "speaker_confirmed": mp.speaker_confirmed}
            for mid, mp in layout.mappings.items()
        },
    }


def _reject_non_finite(value: Any) -> None:
    raise LayoutError(f"profile contains a non-finite number: {value!r}")


def layout_from_dict(data: Any) -> Layout:
    if not isinstance(data, dict):
        raise LayoutError("profile must be a JSON object")
    if data.get("schema") not in (1, 2, SCHEMA_VERSION):
        raise LayoutError(
            f"unsupported profile schema {data.get('schema')!r} (expected {SCHEMA_VERSION})"
        )
    monitors_raw = data.get("monitors")
    if not isinstance(monitors_raw, list):
        raise LayoutError("profile 'monitors' must be a list")
    monitors = []
    for raw in monitors_raw:
        if not isinstance(raw, dict):
            raise LayoutError("monitor entry must be an object")
        try:
            monitors.append(
                Monitor(
                    id=raw["id"],
                    name=raw["name"],
                    x=raw["x"],
                    y=raw["y"],
                    width=raw["width"],
                    height=raw["height"],
                    orientation=raw.get("orientation", "landscape"),
                    is_main=raw.get("is_main", False),
                    speaker_count=raw.get("speaker_count", 2),
                    speaker_edge=raw.get("speaker_edge", "bottom"),
                    room_x=raw.get("room_x"),
                    room_y=raw.get("room_y", 0.0),
                    room_yaw=raw.get("room_yaw", 0.0),
                    room_confirmed=raw.get("room_confirmed", False) if data["schema"] >= 3 else False,
                )
            )
        except KeyError as exc:
            raise LayoutError(f"monitor entry missing {exc}") from exc
    layout = Layout(
        name=data.get("name", "Monitor layout"),
        input_layout=data.get("input_layout", "stereo"),
        viewing_distance_cm=data.get("viewing_distance_cm", 70.0),
        approximate_scale=bool(data.get("approximate_scale", False)),
        reference_mode=data.get("reference_mode", "main") if data["schema"] >= 3 else "main",
        listener_x=data.get("listener_x", 0.0),
        listener_y=data.get("listener_y", data.get("viewing_distance_cm", 70.0)),
        listener_facing=data.get("listener_facing", 0.0),
        listener_confirmed=data.get("listener_confirmed", False),
    )
    for mon in monitors:
        if layout.get(mon.id) is not None:
            raise LayoutError(f"duplicate monitor id {mon.id!r}")
        mon.validate()
    layout.monitors = monitors
    if data["schema"] < 3 and layout.main() is not None:
        layout.listener_x = layout.main().rect().center_x
    peripherals_raw = data.get("peripherals", []) if data["schema"] >= 3 else []
    if not isinstance(peripherals_raw, list):
        raise LayoutError("profile 'peripherals' must be a list")
    for raw in peripherals_raw:
        if not isinstance(raw, dict):
            raise LayoutError("peripheral entry must be an object")
        try:
            peripheral = Peripheral(**raw)
        except (TypeError, KeyError) as exc:
            raise LayoutError(f"invalid peripheral entry: {exc}") from exc
        layout.add_peripheral(peripheral)
    mappings_raw = data.get("mappings", {})
    if not isinstance(mappings_raw, dict):
        raise LayoutError("profile 'mappings' must be an object")
    for mid, mp in mappings_raw.items():
        if not isinstance(mp, dict):
            raise LayoutError("mapping entry must be an object")
        layout.mappings[mid] = DeviceMapping(
            device=mp.get("device"),
            confirmed_speakers=mp.get("confirmed_speakers", 0),
            channel_order=mp.get("channel_order", []) if data.get("schema") >= 2 else [],
            speaker_confirmed=mp.get("speaker_confirmed", False) if data["schema"] >= 3 else False,
        )
    layout.validate()  # cross-checks: one main, no duplicate devices, unknown ids
    return layout


# -- disk IO ----------------------------------------------------------------


def save_profile(layout: Layout, path: Path | str) -> Path:
    """Atomically write the profile.  Never touches audio state."""
    path = Path(path)
    payload = json.dumps(
        layout_to_dict(layout), indent=2, allow_nan=False, sort_keys=False
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_profile(path: Path | str) -> Layout:
    """Load and fully validate a profile.  Raises LayoutError on any problem."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise LayoutError(f"profile not found: {path}") from exc
    except OSError as exc:
        raise LayoutError(f"cannot read profile {path}: {exc}") from exc
    try:
        data = json.loads(text, parse_constant=_reject_non_finite)
    except json.JSONDecodeError as exc:
        raise LayoutError(f"profile is not valid JSON: {exc}") from exc
    return layout_from_dict(data)
