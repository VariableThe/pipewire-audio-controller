"""Input-channel to physical-speaker contribution calculation.

Spatial model (deliberately simple and honest):

* Listener mode uses independent top-down room coordinates and seat facing.
  The front-elevation display arrangement is never treated as room depth.
* Stereo uses lateral spread: FL = 0.5 - 0.45*sin(azimuth), FR =
  0.5 + 0.45*sin(azimuth), then applies both headroom bounds below. Rear
  speakers receive stereo widening, not invented discrete rear channels.
* 5.1 uses canonical azimuths FL/FR +/-30, FC 0, RL/RR +/-120 degrees.
  Raw gain is cosine-squared within 90 degrees. If no substantial coverage
  exists, the channel folds to nearest room speakers (equal split for ties)
  with an explicit warning. LFE is shared at a reduced total gain.
* Legacy main-display mode retains its front-plane horizontal-offset model
  and outermost-side fold. Main mode never interprets vertical stacking as
  depth. It cannot include standalone room speakers.

Hard safety bounds, in order:

1. per input channel: sum of gains over speakers ≤ 1.0;
2. per output speaker (after step 1): sum of *all* incoming channel gains ≤
   ``OUTPUT_HEADROOM`` - otherwise the speaker's gains are scaled down
   uniformly (this only attenuates, so bound 1 still holds);
3. gains are floored to 4 decimals - rounding can never increase a sum, so
   both bounds hold for the exact numbers written to the configuration.

The engine therefore attenuates only; it can never amplify.  And as the
README states: spreading stereo across four speakers is stereo widening, not
discrete rear or object-based surround, and desk geometry derived from
desktop pixel positions is not an acoustic measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .model import Layout, LayoutError

# Input channel sets follow PipeWire's own shipped examples
# (sink-upmix-5.1-filter.conf: "FL FR FC LFE RL RR" - center is FC, not C).
INPUT_CHANNELS: dict[str, list[str]] = {
    "stereo": ["FL", "FR"],
    "5.1": ["FL", "FR", "FC", "LFE", "RL", "RR"],
}
# Canonical horizontal azimuth of each input channel, as seen from the
# listener (0 = straight ahead, + = right).  LFE is non-directional.
CHANNEL_ANGLES: dict[str, float | None] = {
    "FL": -30.0,
    "FR": 30.0,
    "FC": 0.0,
    "LFE": None,
    "RL": -120.0,
    "RR": 120.0,
}
LFE_GAIN_CAP = 0.5  # LFE shared quietly; never a full-range mono sum.
FOLD_GAIN = 0.7  # rear/side-downmix strength into the outermost front speaker
OUTPUT_HEADROOM = 1.0
GAIN_EPSILON = 1e-6
ROUNDING = 4


@dataclass
class ContributionResult:
    """Full panning answer for preview and conf generation."""

    matrix: dict[str, dict[str, float]]  # input channel -> speaker key -> gain
    speaker_keys: list[str]
    channels: list[str]
    warnings: list[str] = field(default_factory=list)

    def row_for(self, channel: str) -> list[float]:
        return [self.matrix[channel][k] for k in self.speaker_keys]

    def speaker_total(self, speaker_key: str) -> float:
        return sum(self.matrix[ch][speaker_key] for ch in self.channels)


def _angle_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _floor_gain(g: float) -> float:
    """Round *down* so digitised sums can never exceed the pre-rounding sums."""
    f = math.floor(g * (10**ROUNDING) + 1e-12) / (10**ROUNDING)
    return f if f > GAIN_EPSILON else 0.0


def compute_contributions(
    layout: Layout, input_layout: str | None = None
) -> ContributionResult:
    """Compute gains from every input channel to every physical speaker."""
    name = input_layout or layout.input_layout
    if name not in INPUT_CHANNELS:
        raise LayoutError(f"unsupported input layout {name!r}")
    layout.validate()
    if not layout.targets():
        raise LayoutError("layout has no speakers or monitors")
    speakers = layout.speakers()
    if not speakers:
        raise LayoutError("layout maps no speakers")
    azimuths = layout.speaker_azimuths()

    channels = INPUT_CHANNELS[name]
    # raw directional gains, before any normalisation
    gains: dict[str, list[float]] = {}
    warnings: list[str] = []
    if layout.reference_mode == "listener":
        if any(not target.room_confirmed for target in layout.targets()):
            warnings.append("Room placement is approximate. Position and confirm every speaker in the listening-area view; display detection does not measure room depth or yaw.")
        if name == "stereo":
            warnings.append("Stereo spread shares left/right content across all placed speakers, including rear positions. It is stereo widening, not discrete surround.")
    ordered_keys = [s.key for s in speakers]
    for ch in channels:
        ch_angle = CHANNEL_ANGLES[ch]
        if ch_angle is None:
            gains[ch] = [LFE_GAIN_CAP / len(speakers)] * len(speakers)
            continue
        row = []
        for az in azimuths:
            if name == "stereo" and layout.reference_mode == "listener":
                # Amplitude-based lateral spread. Front and rear at the same
                # lateral angle share stereo identity, never invented rear
                # channels. A small crossfeed keeps every physical output
                # represented even at exactly +/-90 degrees.
                lateral = math.sin(math.radians(az))
                row.append(0.5 + (-0.45 if ch == "FL" else 0.45) * lateral)
            else:
                d = _angle_diff_deg(ch_angle, az)
                row.append(math.cos(math.radians(d)) ** 2 if d < 90.0 else 0.0)
        if max(row, default=0.0) < 0.001:
            # Documented downmix fold: no speaker faces this channel.  Find the
            # physical extremes over speaker azimuth rather than trusting a
            # particular monitor array order (reordered displays must get
            # the same left-/right-most association as their geometry).
            left_idx, _ = min(
                enumerate(azimuths), key=lambda item: (item[1], ordered_keys[item[0]])
            )
            right_idx, _ = max(
                enumerate(azimuths), key=lambda item: (item[1], ordered_keys[item[0]])
            )
            target = left_idx if ch_angle < 0 else right_idx
            if layout.reference_mode == "listener":
                nearest = min(_angle_diff_deg(ch_angle, a) for a in azimuths)
                targets = [i for i, a in enumerate(azimuths)
                           if abs(_angle_diff_deg(ch_angle, a) - nearest) < 1e-9]
                row = [FOLD_GAIN / len(targets) if i in targets else 0.0
                       for i in range(len(speakers))]
                target = targets[0]
            else:
                row = [0.0] * len(speakers)
                row[target] = FOLD_GAIN
            destinations = ", ".join(ordered_keys[i] for i, gain in enumerate(row) if gain > 0)
            warnings.append(
                f"{ch} has no substantial directional coverage; folded into "
                f"{destinations} at {round(FOLD_GAIN * 100)}% total before headroom scaling; "
                "the matrix shows final per-speaker gains "
                "(mono downmix - this is not discrete surround)."
            )
        gains[ch] = row

    # bound 1: per input channel
    for ch in channels:
        total = sum(gains[ch])
        if total > 1.0:
            gains[ch] = [g / total for g in gains[ch]]

    # bound 2: per output speaker, across all simultaneous inputs (a fully
    # correlated mix must never exceed headroom: 0.75*FL + 0.75*FR would clip)
    attenuated: list[str] = []
    for i, key in enumerate(ordered_keys):
        incoming = sum(gains[ch][i] for ch in channels)
        if incoming > OUTPUT_HEADROOM:
            scale = OUTPUT_HEADROOM / incoming
            for ch in channels:
                gains[ch][i] *= scale
            attenuated.append(key)
    for key in attenuated:
        warnings.append(
            f"{key} receives more than one channel; gains scaled down so the "
            f"speaker's total cannot exceed {OUTPUT_HEADROOM:.1f} headroom."
        )

    matrix: dict[str, dict[str, float]] = {}
    for ch in channels:
        matrix[ch] = {k: _floor_gain(g) for k, g in zip(ordered_keys, gains[ch])}
    return ContributionResult(matrix, ordered_keys, channels, warnings)


def describe_row(result: ContributionResult, channel: str) -> str:
    """Human-readable one-liner for the preview pane, e.g.
    ``FL: monitor-1/L 83% · monitor-1/R 21%``."""
    parts = []
    for key in result.speaker_keys:
        g = result.matrix[channel].get(key, 0.0)
        if g <= 0.0:
            continue
        parts.append(f"{key} {round(g * 100)}%")
    if not parts:
        return f"{channel}: silent"
    return f"{channel}: " + " · ".join(parts)
