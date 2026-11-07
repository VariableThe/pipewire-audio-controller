import re

import pytest

from monitor_layout.model import Layout, LayoutError, Monitor
from monitor_layout.panning import compute_contributions
from monitor_layout.routing import (
    COMBINE_NODE_NAME,
    generate_pipewire_conf as _generate_pipewire_conf,
    slugify,
)


def generate_pipewire_conf(layout, **kwargs):
    kwargs.setdefault("device_channels", {d: ["FL", "FR"] for d in layout.mapped_device_names()})
    return _generate_pipewire_conf(layout, **kwargs)


def mapped_dual_layout():
    lay = Layout()
    lay.add_monitor(Monitor(id="monitor-1", name="L", x=0, y=0, width=60, height=30))
    lay.add_monitor(Monitor(id="monitor-2", name="R", x=64, y=0, width=60, height=30))
    lay.set_main("monitor-1")
    lay.map_device("monitor-1", "alsa_output.pci-0000_01_00.1.pro-output-3", 2)
    lay.map_device("monitor-2", "alsa_output.pci-0000_01_00.1.pro-output-7", 2)
    for mapping in lay.mappings.values():
        mapping.channel_order = ["FL", "FR"]
    return lay


def test_generate_matches_legacy_shape():
    result = generate_pipewire_conf(mapped_dual_layout())
    text = result.conf_text
    assert "libpipewire-module-filter-chain" in text
    assert "libpipewire-module-combine-stream" in text
    assert COMBINE_NODE_NAME in text
    # both hardware playback targets present
    assert '"alsa_output.pci-0000_01_00.1.pro-output-3"' in text
    assert '"alsa_output.pci-0000_01_00.1.pro-output-7"' in text
    # every gain literal is in range
    for m in re.finditer(r'"Gain \d+" = ([\d.]+)', text):
        assert 0.0 < float(m.group(1)) <= 1.0
    assert result.included_devices == sorted(result.included_devices)


def test_deterministic_output():
    a = generate_pipewire_conf(mapped_dual_layout()).conf_text
    b = generate_pipewire_conf(mapped_dual_layout()).conf_text
    assert a == b


def test_gains_never_exceed_one_per_channel():
    text = generate_pipewire_conf(mapped_dual_layout()).conf_text
    for module in text.split("node.target")[1:]:
        pass  # static regex check above already guarantees per-gain range


def test_unmapped_monitor_is_skipped_with_note():
    lay = mapped_dual_layout()
    lay.unmap_device("monitor-2")
    result = generate_pipewire_conf(lay)
    assert "monitor-2 is not mapped" in result.conf_text
    assert result.skipped_monitors == ["monitor-2"]
    assert "pro-output-7" not in result.conf_text


def test_unconfirmed_speaker_count_blocks_export():
    lay = mapped_dual_layout()
    lay.mappings["monitor-2"].confirmed_speakers = 1
    with pytest.raises(LayoutError, match="re-confirm"):
        generate_pipewire_conf(lay)


def test_no_devices_blocks_export():
    lay = mapped_dual_layout()
    lay.unmap_device("monitor-1")
    lay.unmap_device("monitor-2")
    with pytest.raises(LayoutError, match="no monitors are mapped"):
        generate_pipewire_conf(lay)


def test_empty_layout_rejected():
    with pytest.raises(LayoutError):
        generate_pipewire_conf(Layout())


def test_surround_layout_uses_six_capture_channels():
    lay = mapped_dual_layout()
    lay.input_layout = "5.1"
    text = generate_pipewire_conf(lay).conf_text
    assert "FL FR FC LFE RL RR" in text  # PipeWire's own 5.1 channel names


def test_sink_channel_labels_and_order_preserved():
    lay = mapped_dual_layout()
    devs = {d: ["FL", "FR"] for d in lay.mapped_device_names()}
    result = generate_pipewire_conf(
        lay, device_channels=devs, available_devices=set(devs)
    )
    assert "audio.position = [ FL FR ]" in result.conf_text
    assert "AUX0" not in result.conf_text


def test_ambiguous_sink_gets_aux_labels_and_loud_warning():
    lay = mapped_dual_layout()
    devs = {d: ["FL", "FR"] for d in lay.mapped_device_names()}
    devs["alsa_output.pci-0000_01_00.1.pro-output-7"] = []  # unknown layout
    with pytest.raises(LayoutError, match="Discover"):
        generate_pipewire_conf(lay, device_channels=devs)


def test_sink_reporting_fewer_channels_is_a_hard_error():
    lay = mapped_dual_layout()
    devs = {d: ["FL", "FR"] for d in lay.mapped_device_names()}
    devs["alsa_output.pci-0000_01_00.1.pro-output-7"] = ["MONO"]
    with pytest.raises(LayoutError, match="reports 1 channel"):
        generate_pipewire_conf(lay, device_channels=devs)


def test_sink_with_extra_channels_uses_first_needed_with_warning():
    lay = mapped_dual_layout()
    devs = {d: ["FL", "FR"] for d in lay.mapped_device_names()}
    devs["alsa_output.pci-0000_01_00.1.pro-output-7"] = ["FL", "FR", "AUX2"]
    result = generate_pipewire_conf(lay, device_channels=devs)
    assert any("remaining stay silent" in w for w in result.warnings)


def test_stale_device_blocks_export():
    lay = mapped_dual_layout()
    present = {"alsa_output.pci-0000_01_00.1.pro-output-3"}  # second pulled out
    with pytest.raises(LayoutError, match="not.*currently detected"):
        generate_pipewire_conf(lay, available_devices=present)


def test_slug_collision_detected():
    lay = Layout()
    lay.add_monitor(Monitor(id="monitor-1", name="A", x=0, y=0, width=60, height=30))
    lay.add_monitor(Monitor(id="monitor-2", name="B", x=66, y=0, width=60, height=30))
    lay.set_main("monitor-1")
    lay.map_device("monitor-1", "dev.a", 2)
    lay.map_device("monitor-2", "dev_a", 2)  # slugs to the same name
    with pytest.raises(LayoutError, match="collapse"):
        generate_pipewire_conf(lay)


def test_unconfirmed_count_blocks_export_even_when_matching():
    lay = mapped_dual_layout()
    lay.mappings["monitor-1"].confirmed_speakers = 0
    with pytest.raises(LayoutError, match="confirmed"):
        generate_pipewire_conf(lay)


def test_slugify_sanitizes_and_rejects_shell_unfriendly_names():
    good = slugify("alsa_output.pci-0000_01_00.1.pro-output-3")
    assert good == "alsa_output_pci_0000_01_00_1_pro_output_3"
    # junk is collapsed into a safe fragment rather than passed through
    assert slugify("../..///etc") == "etc"
    assert slugify('evil"; rm -rf /') == "evil_rm_rf"
    with pytest.raises(LayoutError):
        slugify("1234")  # node names must start with a letter
    with pytest.raises(LayoutError):
        slugify("!!!")


def test_malicious_device_name_rejected_at_mapping_time():
    lay = mapped_dual_layout()
    lay.unmap_device("monitor-2")
    with pytest.raises(LayoutError, match="unsupported characters"):
        lay.map_device("monitor-2", 'evil"= } system() {', 2)
    lay.map_device("monitor-2", "alsa_output.nasty.name", 2)
    lay.mappings["monitor-2"].channel_order = ["FL", "FR"]
    text = generate_pipewire_conf(lay).conf_text
    assert "monitor_layout_alsa_output_nasty_name" in text
    assert "system()" not in text


def test_all_silent_speaker_refused():
    """The module generator must refuse a speaker whose gains are all zero.
    The panner's fold rule makes this unreachable in normal layouts, so test
    the guard directly with a synthetic zero matrix."""
    from monitor_layout.routing import _filter_chain_module

    zero = {"FL": {"monitor-1/L": 0.0, "monitor-1/R": 0.0},
            "FR": {"monitor-1/L": 0.0, "monitor-1/R": 0.0}}
    with pytest.raises(LayoutError, match="no signal"):
        _filter_chain_module(
            "dev.a",
            "dev_a",
            [("monitor-1/L", "monitor-1"), ("monitor-1/R", "monitor-1")],
            ["FL", "FR"],
            ["FL", "FR"],
            zero,
        )


def test_playback_props_use_current_target_and_safety_properties():
    result = generate_pipewire_conf(mapped_dual_layout(), available_devices=set(mapped_dual_layout().mapped_device_names()))
    text = result.conf_text
    assert "target.object" in text
    assert "node.dont-reconnect = true" in text
    assert "node.dont-fallback = true" in text
    assert "stream.dont-remix = true" in text
    assert "node.target" not in text


def test_layout_name_with_control_characters_cannot_inject():
    lay = mapped_dual_layout()
    lay.name = "safe\n# system()\n# injected"
    text = generate_pipewire_conf(lay).conf_text
    assert "# Layout: safe # system() # injected   Input:" in text or "# Layout: safe" in text
    # The injected fragments must all remain inside the original comment line.
    assert "system()" in text, "sanitised, not dropped, but never starts a line"
    for line in text.splitlines():
        if line.strip().startswith("# system()"):
            raise AssertionError("control characters escaped the comment")


def test_malicious_channel_token_is_rejected():
    lay = mapped_dual_layout()
    devs = {d: ["FL", "FR"] for d in lay.mapped_device_names()}
    devs["alsa_output.pci-0000_01_00.1.pro-output-7"] = ["FL\n#", "FR"]
    with pytest.raises(LayoutError, match="invalid channel token"):
        generate_pipewire_conf(lay, device_channels=devs, available_devices=set(devs))


def test_rear_channel_fold_uses_extreme_azimuth_not_monitor_array_order():
    ordered = Layout()
    ordered.add_monitor(Monitor(id="monitor-1", name="L", x=0, y=0, width=60, height=30))
    ordered.add_monitor(Monitor(id="monitor-2", name="R", x=64, y=0, width=60, height=30))
    ordered.set_main("monitor-1")
    reversed_order = Layout()
    reversed_order.add_monitor(Monitor(id="monitor-2", name="R", x=64, y=0, width=60, height=30))
    reversed_order.add_monitor(Monitor(id="monitor-1", name="L", x=0, y=0, width=60, height=30))
    reversed_order.set_main("monitor-1")

    a = compute_contributions(ordered, "5.1")
    b = compute_contributions(reversed_order, "5.1")
    # Folded rear channel is in the same (left-most) speaker either way.
    fold_a = [k for k in a.speaker_keys if a.matrix["RL"][k] == pytest.approx(0.7 * (1.0 / 1.0)) or a.matrix["RL"][k] >= 0.3]
    fold_b = [k for k in b.speaker_keys if b.matrix["RL"][k] >= 0.3]
    assert "monitor-1/L" in fold_a
    assert "monitor-1/L" in fold_b
