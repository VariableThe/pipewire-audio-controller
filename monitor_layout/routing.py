"""PipeWire filter-chain + combine-stream configuration generation.

Generalises the hand-written ``dual-monitor.conf``: one ``filter-chain``
module per mapped PipeWire output device (capture = the input channel set,
playback = that device's AUX ports in speaker order) plus one
``combine-stream`` sink that aggregates all of them as a single selectable
system output.

Guarantees:

* Deterministic output for the same inputs (no timestamps, stable ordering).
* All node names come from an allow-listed slug function - no injection of
  quotes, braces or path characters into the SPA config syntax.
* Gains are pre-clamped by :mod:`monitor_layout.panning`; generation refuses
  a device whose speakers would all be silent.
* Unmapped monitors are skipped with a comment, mapping mismatches raise.

This module writes nothing by itself; see :mod:`monitor_layout.apply` for the
guarded install/rollback helper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Layout, LayoutError
from .panning import INPUT_CHANNELS, ContributionResult, compute_contributions

COMBINE_NODE_NAME = "monitor_layout_combined"
_COMBINE_DESCRIPTION = "Monitor Layout Combined Sink"

_SLUG_OK = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_OK.sub("_", text.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:48]
    if not slug or not slug[0].isalpha():
        raise LayoutError(f"cannot derive a safe node name from {text!r}")
    return slug


def _sanitize_comment(value: object) -> str:
    """Return a header/comment value that cannot break out of one line."""
    text = str(value)
    cleaned = "".join(" " if (ord(ch) < 0x20 or ch == "\x7f") else ch for ch in text)
    return " ".join(cleaned.split())


_CHANNEL_TOKEN_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _safe_channel_token(value: object, device: str) -> str:
    if not isinstance(value, str) or not _CHANNEL_TOKEN_OK.fullmatch(value):
        raise LayoutError(
            f"{device}: invalid channel token {value!r}; channel labels must be "
            "simple PipeWire channel names without spaces, quotes or brackets"
        )
    return value


def spa_string(value: str) -> str:
    """Render a string safe for a double-quoted SPA property value."""
    if re.search(r'["\\\n\r\t{}=]', value):
        raise LayoutError(f"unsafe characters for SPA property: {value!r}")
    return f'"{value}"'


def _fmt_gain(g: float) -> str:
    if not (0.0 <= g <= 1.0):
        raise LayoutError(f"gain out of range: {g}")
    return f"{g:.4f}".rstrip("0").rstrip(".")


@dataclass
class GenerationResult:
    conf_text: str
    included_devices: list[str] = field(default_factory=list)
    skipped_monitors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _filter_chain_module(
    device: str,
    device_slug: str,
    speakers: list[tuple[str, str]],  # (speaker key, monitor id) in playback order
    playback_channels: list[str],
    channels: list[str],
    matrix: dict[str, dict[str, float]],
) -> str:
    sink_name = f"monitor_layout_{device_slug}"
    capture_positions = " ".join(channels)
    playback_positions = " ".join(playback_channels)

    copy_nodes = []
    inputs = []
    for i, ch in enumerate(channels):
        copy_nodes.append(
            f'                      {{ type = builtin name = copy_{i} label = copy }}'
        )
        inputs.append(f'"copy_{i}:In"')

    mixer_nodes = []
    links = []
    outputs = []
    for spk_idx, (spk_key, _mid) in enumerate(speakers):
        gains = [matrix[ch].get(spk_key, 0.0) for ch in channels]
        if sum(gains) <= 1e-6:
            raise LayoutError(
                f"speaker {spk_key} would receive no signal; refusing to "
                "generate a silent output"
            )
        ctl = " ".join(
            f'"Gain {i + 1}" = {_fmt_gain(g)}'
            for i, g in enumerate(gains)
            if g > 0.0
        )
        mixer_nodes.append(
            f"                      {{ type = builtin name = mix_{spk_idx} label = mixer "
            f"control = {{ {ctl} }} }}"
        )
        for i, g in enumerate(gains):
            if g <= 0.0:
                continue
            links.append(
                f"                      {{ output = \"copy_{i}:Out\" "
                f"input = \"mix_{spk_idx}:In {i + 1}\" }}"
            )
        outputs.append(f'"mix_{spk_idx}:Out"')

    nodes = "\n".join(copy_nodes + mixer_nodes)
    link_block = "\n".join(links)
    speaker_note = ", ".join(
        f"{_sanitize_comment(k)} -> {_sanitize_comment(ch)}"
        for (k, _m), ch in zip(speakers, playback_channels)
    )
    return f"""    # {_sanitize_comment(device)}  (speakers: {speaker_note})
    {{ name = libpipewire-module-filter-chain
      args = {{
          node.description = {spa_string(f"Monitor Layout - {device}")}
          media.name       = {spa_string(f"Monitor Layout - {device}")}
          filter.graph = {{
              nodes = [
{nodes}
              ]
              inputs  = [ {' '.join(inputs)} ]
              links = [
{link_block}
              ]
              outputs = [ {' '.join(outputs)} ]
          }}
          capture.props = {{
              node.name      = {sink_name}
              media.class    = "Audio/Sink"
              audio.position = [ {capture_positions} ]
          }}
          playback.props = {{
              node.name = "{sink_name}_playback"
              target.object = {spa_string(device)}
              node.dont-reconnect = true
              node.dont-fallback = true
              stream.dont-remix = true
              audio.position = [ {playback_positions} ]
          }}
      }}
    }}
"""


def generate_pipewire_conf(
    layout: Layout,
    result: ContributionResult | None = None,
    device_channels: dict[str, list[str]] | None = None,
    available_devices: set[str] | None = None,
) -> GenerationResult:
    """Render a pipewire.conf.d fragment for the current layout.

    ``device_channels``: sink-name -> reported channel labels (from
    :mod:`monitor_layout.audiodev`); when omitted, every device is treated
    as unverified and export is refused. Physical order must be explicitly
    confirmed in each mapping; discovery order is never physical calibration.
    ``available_devices``: sink names visible in the last discovery pass;
    when given, mapping a device that is not currently present is a hard
    error (stale mappings never produce routing).  Pass None only for
    offline authoring where the check is impossible; warnings will say so.
    """
    layout.validate()
    if not layout.targets():
        raise LayoutError("cannot generate a configuration for an empty layout")
    result = result or compute_contributions(layout)
    channels = INPUT_CHANNELS[layout.input_layout]

    devices: dict[str, list[tuple[str, str]]] = {}
    skipped: list[str] = []
    for mon in layout.targets():
        mapping = layout.mappings.get(mon.id)
        if mapping is None or mapping.device is None:
            skipped.append(mon.id)
            continue
        if available_devices is not None and mapping.device not in available_devices:
            raise LayoutError(
                f"{mon.id} is mapped to {mapping.device!r}, which is not "
                "currently detected.  Reconnect it or remove the mapping "
                "before exporting."
            )
        if mapping.confirmed_speakers != mon.speaker_count:
            raise LayoutError(
                f"{mon.id}: user confirmed {mapping.confirmed_speakers} speaker(s) "
                f"but the layout declares {mon.speaker_count}; re-confirm after "
                "changing the count"
            )
        if mapping.confirmed_speakers == 0:
            raise LayoutError(
                f"{mon.id}: speaker count has not been confirmed; cannot guess "
                "the number of physical drivers"
            )
        devices.setdefault(mapping.device, [])
    for spk in sorted(layout.speakers(), key=lambda s: (s.monitor_id, s.slot)):
        mapping = layout.mappings.get(spk.monitor_id)
        if mapping and mapping.device is not None:
            devices[mapping.device].append((spk.key, spk.monitor_id))
    if not devices:
        raise LayoutError("no monitors are mapped to an output device")

    # Never silently lose an input whose only nonzero destinations were unmapped.
    included_keys = {key for entries in devices.values() for key, _ in entries}
    for channel in channels:
        if not any(result.matrix[channel].get(key, 0) > 0 for key in included_keys):
            raise LayoutError(f"{channel} has no mapped audible destination; map speakers or change layout")
    if not devices:
        raise LayoutError("no monitors are mapped to an output device")

    # Stable per-run slugs with collision detection.
    module_slugs: dict[str, str] = {}
    by_slug: dict[str, str] = {}
    for device in sorted(devices):
        slug = slugify(device)
        if slug in by_slug and by_slug[slug] != device:
            raise LayoutError(
                f"devices {by_slug[slug]!r} and {device!r} collapse to the same "
                f"internal node name {slug!r}; rename one device mapping"
            )
        by_slug[slug] = device
        module_slugs[device] = slug

    extra_warnings: list[str] = []
    playback_labels: dict[str, list[str]] = {}
    for device in sorted(devices):
        reported = (device_channels or {}).get(device)
        mapping = next(mp for mp in layout.mappings.values() if mp.device == device)
        mapping.validate_channel_order(len(devices[device]), reported)
        playback_labels[device] = list(mapping.channel_order)
        if len(reported) > len(mapping.channel_order):
            extra_warnings.append(f"{device}: unused channels remaining stay silent.")
    if available_devices is None:
        extra_warnings.append(
            "Exported without a device present-check; confirm every mapped "
            "sink exists before installing."
        )
    header = [
        "# Generated by monitor-layout - reviewed by hand before installing.",
        f"# Layout: {_sanitize_comment(layout.name)}   Input: {layout.input_layout}",
        "# Gains are amplitude-normalised per input channel (sum <= 1.0).",
        "# Stereo widening across extra speakers is not discrete surround;",
        "# desktop geometry is desk geometry, not an acoustic measurement.",
    ]
    for mon_id in skipped:
        header.append(f"# NOTE: monitor {_sanitize_comment(mon_id)} is not mapped to an output and is skipped.")
    header.append("")
    header.append("context.modules = [")

    body = []
    for device in sorted(devices):
        body.append(
            _filter_chain_module(
                device,
                module_slugs[device],
                devices[device],
                playback_labels[device],
                channels,
                result.matrix,
            )
        )

    rules = []
    for device in sorted(devices):
        sink_name = f"monitor_layout_{module_slugs[device]}"
        rules.append(
            f"""              {{ matches = [ {{ node.name = {spa_string(sink_name)} }} ]
                actions = {{ create-stream = {{ audio.position = [ {' '.join(channels)} ] }} }}
              }}"""
        )
    rules_block = "\n".join(rules)
    body.append(f"""    # Combined sink: pick this as the system output.
    {{ name = libpipewire-module-combine-stream
      args = {{
          combine.mode = sink
          combine.latency-compensate = true
          node.name = {COMBINE_NODE_NAME}
          node.description = {spa_string(_COMBINE_DESCRIPTION)}
          combine.props = {{
              audio.position = [ {' '.join(channels)} ]
          }}
          stream.rules = [
{rules_block}
          ]
      }}
    }}
""")
    conf = "\n".join(header) + "\n".join(body) + "]\n"
    _static_check(conf)
    return GenerationResult(
        conf_text=conf,
        included_devices=sorted(devices),
        skipped_monitors=skipped,
        warnings=list(result.warnings) + extra_warnings,
    )


def _static_check(text: str) -> None:
    """Cheap structural sanity: balanced brackets outside string literals."""
    depth = {"[": 0, "{": 0}
    pairs = {"[": "]", "{": "}"}
    stack: list[str] = []
    in_str = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == "#":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            if c == '"':
                in_str = True
            elif c in pairs:
                stack.append(pairs[c])
            elif c in "]}":
                if not stack or stack.pop() != c:
                    raise LayoutError("generated configuration is structurally invalid")
        i += 1
    if stack or in_str:
        raise LayoutError("generated configuration is structurally invalid")
