import pytest

from monitor_layout.model import Layout, LayoutError, Monitor
from monitor_layout.panning import (
    CHANNEL_ANGLES,
    INPUT_CHANNELS,
    compute_contributions,
    describe_row,
)


def dual_layout(main="monitor-1"):
    lay = Layout()
    lay.add_monitor(Monitor(id="monitor-1", name="L", x=0, y=0, width=60, height=30))
    lay.add_monitor(Monitor(id="monitor-2", name="R", x=66, y=0, width=60, height=30))
    lay.set_main(main)
    return lay


def assert_output_bounds(result):
    """Real output-sample bound: a speaker's summed gains over all channels
    never exceed headroom, checking the exact rounded values that would be
    written to the configuration."""
    for key in result.speaker_keys:
        assert result.speaker_total(key) <= 1.0 + 1e-9, key
    for ch in result.channels:
        row = result.matrix[ch]
        assert all(0.0 <= g <= 1.0 for g in row.values())
        assert sum(row.values()) <= 1.0 + 1e-9


def test_no_amplification_invariant_both_bounds():
    lay = Layout()
    lay.add_monitor(Monitor(id="monitor-1", name="L", x=0, y=0, width=60, height=30))
    lay.add_monitor(Monitor(id="monitor-2", name="R", x=66, y=0, width=60, height=30))
    layouts = []
    for main in ("monitor-1", "monitor-2"):
        lay.set_main(main)
        layouts.append((lay.input_layout, None))
        assert_output_bounds(compute_contributions(lay))
        assert_output_bounds(compute_contributions(lay, "5.1"))


def test_centered_mono_speaker_cannot_clip():
    """One centered speaker receiving 0.75*FL + 0.75*FR would clip for
    correlated full-scale input; the engine must attenuate it to a bounded
    50/50 mono blend."""
    lay = Layout()
    lay.add_monitor(
        Monitor(id="monitor-1", name="bar", width=90, height=30, speaker_count=1)
    )
    result = compute_contributions(lay)
    for ch in ("FL", "FR"):
        assert result.matrix[ch]["monitor-1/C"] == pytest.approx(0.5, abs=1e-4)
    assert result.speaker_total("monitor-1/C") == pytest.approx(1.0, abs=1e-3)
    assert any("scaled down" in w for w in result.warnings)


def test_mirror_symmetry_single_monitor():
    lay = Layout()
    lay.add_monitor(Monitor(id="monitor-1", name="solo", width=60, height=30))
    result = compute_contributions(lay)
    m = result.matrix
    assert m["FL"]["monitor-1/L"] == pytest.approx(m["FR"]["monitor-1/R"])
    assert m["FL"]["monitor-1/R"] == pytest.approx(m["FR"]["monitor-1/L"])
    assert m["FL"]["monitor-1/L"] > m["FL"]["monitor-1/R"]  # left favours FL


def test_stereo_widening_matches_legacy_spirit():
    """Outer speakers keep channel identity stronger than inner ones -
    the computed version of the hand-written dual-monitor.conf blend."""
    lay = dual_layout("monitor-1")
    result = compute_contributions(lay)
    m = result.matrix
    assert m["FL"]["monitor-1/L"] > m["FL"]["monitor-2/R"]
    assert m["FR"]["monitor-2/R"] > m["FR"]["monitor-1/L"]
    assert m["FL"]["monitor-2/L"] > 0.0  # inner blend is present


def test_main_monitor_changes_reference_and_gains():
    a = compute_contributions(dual_layout("monitor-1"))
    b = compute_contributions(dual_layout("monitor-2"))
    key = "monitor-1/L"
    assert a.matrix["FL"][key] != pytest.approx(b.matrix["FL"][key])


def test_vertical_stack_is_elevation_not_position_depth():
    """A monitor stacked above the main one shares the azimuth of its
    horizontal neighbours; stacking must not be read as depth (behind/in
    front of the listener)."""
    flat = Layout()
    flat.add_monitor(Monitor(id="monitor-1", name="main", width=60, height=30))
    flat.add_monitor(Monitor(id="monitor-2", name="side", x=66, y=0, width=60, height=30))
    stacked = Layout()
    stacked.add_monitor(Monitor(id="monitor-1", name="main", width=60, height=30))
    stacked.add_monitor(Monitor(id="monitor-2", name="side", x=66, y=-34, width=60, height=30))
    a = compute_contributions(flat).matrix
    b = compute_contributions(stacked).matrix
    for ch in ("FL", "FR"):
        for key in a[ch]:
            assert a[ch][key] == pytest.approx(b[ch][key], abs=1e-4)


def test_portrait_monitor_speaker_geometry():
    lay = Layout()
    lay.add_monitor(
        Monitor(
            id="monitor-1", name="main", width=60, height=34, x=0, y=0
        )
    )
    lay.add_monitor(
        Monitor(
            id="monitor-2",
            name="side",
            x=66,
            y=0,
            width=60,
            height=34,
            orientation="portrait",
        )
    )
    result = compute_contributions(lay)
    assert_output_bounds(result)
    # rotated rect is 34 wide: its speaker anchors sit within [66, 100]
    anchors = lay.speaker_anchors()
    side = [a for a in anchors if 64 < a[0] < 102]
    assert len(side) == 2
    assert side[0][0] == pytest.approx(66 + 34 / 4)


def test_surround_rear_channels_fold_or_bleed_with_warning():
    """With the reference at the left monitor this layout is asymmetric:
    RL (-120 deg) finds no speaker within its hemisphere and folds into the
    outermost-left speaker at FOLD_GAIN with an explicit warning, while RR
    (+120 deg) is 71 deg off the outermost-right speaker and bleeds there
    gently instead.  Both are documented, bounded, and never silently lost."""
    lay = dual_layout()
    lay.input_layout = "5.1"
    result = compute_contributions(lay)
    assert result.channels == INPUT_CHANNELS["5.1"]
    assert "FC" in result.matrix  # PipeWire's center label
    folds = [w for w in result.warnings if "folded" in w]
    assert any("RL" in w for w in folds)
    # fold lands in the outermost-left speaker, then the per-output
    # headroom pass scales the whole speaker mix down (documented warning)
    assert 0.3 < result.matrix["RL"]["monitor-1/L"] <= 0.7
    assert any("headroom" in w for w in result.warnings)
    rr_outer = result.matrix["RR"]["monitor-2/R"]
    assert 0.0 < rr_outer < 0.25  # gentle rear bleed, not a fold
    lfe = result.matrix["LFE"]
    # LFE targets a 0.5 total spread; per-output headroom scaling can only
    # reduce it further, never increase it.
    assert 0.3 < sum(lfe.values()) <= 0.5 + 1e-9
    assert_output_bounds(result)


def test_fc_center_dominates_center_speakers():
    lay = dual_layout("monitor-1")
    result = compute_contributions(lay, "5.1")
    fc = result.matrix["FC"]
    assert sum(fc.values()) <= 1.0 + 1e-9
    assert fc["monitor-1/R"] > 0 or fc["monitor-1/L"] > 0


def test_unknown_input_layout_rejected():
    with pytest.raises(LayoutError):
        compute_contributions(dual_layout(), "7.1.4-atmos")


def test_empty_layout_rejected():
    with pytest.raises(LayoutError):
        compute_contributions(Layout())


def test_describe_row_formats_percentages():
    import re

    result = compute_contributions(dual_layout())
    text = describe_row(result, "FL")
    assert text.startswith("FL:")
    pcts = [int(s) for s in re.findall(r"(\d+)%", text)]
    assert pcts and sum(pcts) <= 100


def test_rounding_never_breaks_bounds_randomised():
    import random

    rng = random.Random(7)
    for _ in range(25):
        lay = Layout(name="fuzz")
        for i in range(rng.randint(1, 4)):
            lay.add_monitor(
                Monitor(
                    id=f"monitor-{i + 1}",
                    name=f"m{i}",
                    x=rng.uniform(-50, 200),
                    y=rng.uniform(-60, 60),
                    width=rng.uniform(30, 90),
                    height=rng.uniform(20, 50),
                    orientation=rng.choice(["landscape", "portrait"]),
                    speaker_count=rng.randint(1, 2),
                )
            )
        assert_output_bounds(compute_contributions(lay))
        assert_output_bounds(compute_contributions(lay, "5.1"))


def test_channel_angles_cover_supported_layouts():
    for chans in INPUT_CHANNELS.values():
        for ch in chans:
            assert ch in CHANNEL_ANGLES
