import pytest
from conftest import fixture_text

from monitor_layout import audiodev
from monitor_layout.model import LayoutError


def test_parse_pw_dump_fixture_sinks():
    sinks = audiodev.parse_pw_dump(fixture_text("pw-dump-sinks.json"))
    names = [s.name for s in sinks]
    assert names == [
        "alsa_output.pci-0000_01_00.1.pro-output-3",
        "alsa_output.pci-0000_01_00.1.pro-output-7",
        "alsa_output.usb-ddHiFi.pro-output-0",
        "dual_monitor_sink",
    ]
    hw = sinks[0]
    assert hw.is_virtual is False
    assert hw.channels == ["FL", "FR"]
    virt = sinks[3]
    assert virt.is_virtual is True
    # ports associated by node.id
    assert sinks[0].ports == ["hdmi-output-0"]
    assert sinks[1].ports == ["hdmi-output-3"]


def test_parse_pw_dump_ignores_sources():
    sinks = audiodev.parse_pw_dump(fixture_text("pw-dump-sinks.json"))
    assert all(s.name != "alsa_input.usb-ddHiFi.pro-input-0" for s in sinks)


def test_parse_pw_dump_empty_report():
    result = audiodev.parse_pw_dump(fixture_text("pw-dump-empty.json"))
    assert result == []


def test_parse_pw_dump_rejects_garbage():
    with pytest.raises(LayoutError):
        audiodev.parse_pw_dump("<xml/>")
    with pytest.raises(LayoutError):
        audiodev.parse_pw_dump('{"not": "a list"}')


def test_parse_rejects_channel_flood():
    payload = (
        '[{"id":1,"type":"PipeWire:Interface:Node","info":{"props":{'
        '"media.class":"Audio/Sink","node.name":"flood.sink",'
        '"audio.position":"' + ",".join(["AUX"] * 64) + '"}}}]'
    )
    with pytest.raises(LayoutError):
        audiodev.parse_pw_dump(payload)


def test_discover_reports_missing_tool():
    result = audiodev.discover_sinks(runner=lambda argv: (_ for _ in ()).throw(FileNotFoundError()))
    assert result.error and "not installed" in result.error
    assert result.sinks == []


def test_discover_reports_empty():
    result = audiodev.discover_sinks(runner=lambda argv: fixture_text("pw-dump-empty.json"))
    assert result.error and "no audio sinks" in result.error


def test_channels_are_raw_not_speaker_truth():
    """The parser must not invent driver truth: a sink's channels list is
    whatever PipeWire reported, kept verbatim for the user to confirm."""
    sinks = audiodev.parse_pw_dump(fixture_text("pw-dump-sinks.json"))
    hw = sinks[0]
    assert hw.channels == ["FL", "FR"]  # verbatim
    # and nothing in the model equates that with two physical speakers
    assert hasattr(hw, "ports")


def peripheral_node(props, node_id=1):
    return {"id": node_id, "type": "PipeWire:Interface:Node", "info": {"props": {
        "media.class": "Audio/Sink", "node.name": f"output.{node_id}", **props,
    }}}


def parse_peripheral(*entries):
    import json
    return audiodev.parse_pw_dump(json.dumps(entries))[0]


@pytest.mark.parametrize("form", ["headphone", "headphones", "headset"])
def test_known_headphones_never_suggested(form):
    sink = parse_peripheral(peripheral_node({
        "device.form-factor": form, "device.api": "bluez5",
        "api.bluez5.profile": "a2dp-sink",
    }))
    assert sink.kind == "headphones"
    assert sink.transport == "bluetooth"
    assert not sink.is_virtual
    assert not sink.auto_speaker_candidate
    assert not sink.requires_speaker_confirmation


def test_bluetooth_speaker_metadata_from_device_reference():
    device = {"id": 20, "type": "PipeWire:Interface:Device", "info": {"props": {
        "device.api": "bluez5", "device.form-factor": "speaker",
    }}}
    sink = parse_peripheral(device, peripheral_node({"device.id": "20"}))
    assert sink.kind == "speaker"
    assert sink.transport == "bluetooth"
    assert sink.auto_speaker_candidate
    assert not sink.requires_speaker_confirmation


def test_a2dp_and_product_names_do_not_classify_speaker_or_headphones():
    sink = parse_peripheral(peripheral_node({
        "node.description": "Super Speaker Headphones", "device.api": "bluez5",
        "api.bluez5.profile": "a2dp-sink",
    }))
    assert sink.kind == "unknown"
    assert sink.requires_speaker_confirmation
    assert not sink.auto_speaker_candidate


def test_usb_speaker_and_node_metadata_override_device_metadata():
    device = {"id": 20, "type": "PipeWire:Interface:Device", "info": {"props": {
        "device.api": "alsa", "device.bus": "usb", "device.form-factor": "speaker",
    }}}
    speaker = parse_peripheral(device, peripheral_node({"device.id": 20}))
    assert speaker.auto_speaker_candidate and speaker.transport == "usb"
    headphones = parse_peripheral(device, peripheral_node({
        "device.id": 20, "device.form-factor": "headphone",
    }))
    assert headphones.kind == "headphones"
    assert not headphones.auto_speaker_candidate


@pytest.mark.parametrize("flag", [True, "true", "1"])
def test_virtual_metadata_never_suggests_physical_speaker(flag):
    sink = parse_peripheral(peripheral_node({
        "device.api": "alsa", "device.form-factor": "speaker", "node.virtual": flag,
    }))
    assert sink.is_virtual
    assert not sink.auto_speaker_candidate
    assert not sink.requires_speaker_confirmation


def test_string_false_is_not_a_virtual_flag():
    sink = parse_peripheral(peripheral_node({"device.api": "alsa", "node.virtual": "false"}))
    assert not sink.is_virtual
    assert sink.requires_speaker_confirmation


def test_generated_combined_sink_is_not_a_physical_candidate():
    sink = parse_peripheral(peripheral_node({"device.form-factor": "speaker"}))
    assert sink.is_virtual
    assert not sink.auto_speaker_candidate
    assert not sink.requires_speaker_confirmation
