"""Read-only PipeWire output discovery.

Parses ``pw-dump`` JSON into a list of audio sink nodes with the channel
positions they expose.  The model deliberately keeps ``channels`` as a raw
reported list: channels reported by a sink are *not* assumed to be physical
drivers, and sink/port names are never silently equated with monitor brands.
The GUI asks the user to confirm counts and placements explicitly.

``discover_sinks`` runs the read-only ``pw-dump`` command with a timeout and
reports failure in-band (never raises).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from .model import LayoutError

COMMAND_TIMEOUT = 5.0
MAX_CHANNELS = 32


@dataclass
class AudioSink:
    node_id: int
    name: str  # stable node.name, used as the mapping identity
    description: str
    channels: list[str] = field(default_factory=list)
    is_virtual: bool = False
    ports: list[str] = field(default_factory=list)  # port.name strings, if known

    kind: str = "unknown"  # speaker, headphones, or unknown; never inferred from codec
    transport: str = "unknown"
    form_factor: str = ""

    @property
    def auto_speaker_candidate(self) -> bool:
        return not self.is_virtual and self.kind == "speaker"

    @property
    def requires_speaker_confirmation(self) -> bool:
        return not self.is_virtual and self.kind == "unknown"

    @property
    def label(self) -> str:
        ch = f", {len(self.channels)} ch" if self.channels else ""
        kind = "virtual" if self.is_virtual else f"{self.kind}, {self.transport}"
        return f"{self.description} ({kind}{ch})"


@dataclass
class DiscoveryResult:
    sinks: list[AudioSink] = field(default_factory=list)
    error: Optional[str] = None


def parse_pw_dump(text: str) -> list[AudioSink]:
    """Parse ``pw-dump`` JSON into sinks with channels and ports."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LayoutError(f"pw-dump output is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise LayoutError("pw-dump output must be a JSON array")

    # Device objects often carry BlueZ form factor/API, while nodes only
    # reference them. Do not classify by product name or A2DP profile.
    devices = {
        str(entry.get("id")): (entry.get("info") or {}).get("props") or {}
        for entry in data
        if isinstance(entry, dict) and entry.get("type") == "PipeWire:Interface:Device"
    }
    ports_by_node: dict[int, list[str]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "PipeWire:Interface:Port":
            continue
        info = entry.get("info") or {}
        props = info.get("props") or {}
        node_id = props.get("node.id")
        port_name = props.get("port.name")
        if isinstance(node_id, int) and isinstance(port_name, str) and port_name:
            if props.get("port.direction") == "in":
                ports_by_node.setdefault(node_id, []).append(port_name)

    sinks: list[AudioSink] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "PipeWire:Interface:Node":
            continue
        info = entry.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        name = props.get("node.name")
        if not isinstance(name, str) or not name:
            continue
        description = props.get("node.description") or name
        device_props = devices.get(str(props.get("device.id")), {})
        metadata = {**device_props, **props}
        form_factor = str(metadata.get("device.form-factor", "")).lower()
        kind = (
            "headphones" if form_factor in {"headphone", "headphones", "headset"}
            else "speaker" if form_factor in {"speaker", "speakers"}
            else "unknown"
        )
        api = str(metadata.get("device.api", "")).lower()
        bus = str(metadata.get("device.bus", "")).lower()
        transport = (
            "bluetooth" if api in {"bluez", "bluez5"} or bus == "bluetooth"
            or any(key.startswith("api.bluez5.") for key in metadata)
            else bus if bus in {"usb", "pci"}
            else "alsa" if api == "alsa" or "api.alsa.path" in metadata
            else "unknown"
        )
        # Explicit virtual markers beat any inherited hardware metadata.
        # Missing device.name alone must not hide real Bluetooth sinks.
        virtual_flag = str(metadata.get("node.virtual", False)).lower() in {"true", "1"}
        hardware = bool(device_props) or any(
            key in metadata for key in ("device.name", "api.alsa.path", "api.bluez5.address")
        ) or api in {"alsa", "bluez", "bluez5"}
        is_virtual = virtual_flag or not hardware
        raw_pos = props.get("audio.position")
        channels: list[str] = []
        if isinstance(raw_pos, str) and raw_pos.strip():
            channels = [c.strip() for c in raw_pos.split(",") if c.strip()]
            if len(channels) > MAX_CHANNELS:
                raise LayoutError(
                    f"sink {name!r} reports {len(channels)} channels (max {MAX_CHANNELS})"
                )
        node_id = entry.get("id")
        sinks.append(
            AudioSink(
                node_id=node_id if isinstance(node_id, int) else -1,
                name=name,
                description=str(description),
                channels=channels,
                is_virtual=is_virtual,
                kind=kind,
                transport=transport,
                form_factor=form_factor,
                ports=ports_by_node.get(node_id, []) if isinstance(node_id, int) else [],
            )
        )
    return sinks


RunFn = Callable[[list[str]], str]


def _default_runner(argv: list[str]) -> str:
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
    )
    if proc.returncode != 0:
        raise LayoutError(f"{argv[0]} exited with {proc.returncode}")
    return proc.stdout


def discover_sinks(runner: Optional[RunFn] = None) -> DiscoveryResult:
    """Run read-only discovery; errors are reported, not raised."""
    run = runner or _default_runner
    try:
        sinks = parse_pw_dump(run(["pw-dump"]))
        if not sinks:
            return DiscoveryResult(
                sinks=[], error="pw-dump reported no audio sinks (is PipeWire running?)"
            )
        return DiscoveryResult(sinks=sinks)
    except FileNotFoundError:
        return DiscoveryResult(sinks=[], error="pw-dump is not installed.")
    except (LayoutError, subprocess.SubprocessError, OSError) as exc:
        return DiscoveryResult(sinks=[], error=str(exc))
