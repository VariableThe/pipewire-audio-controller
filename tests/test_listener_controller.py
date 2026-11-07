import pytest
from pathlib import Path
from monitor_layout.controller import LayoutController
from monitor_layout.audiodev import AudioSink
from monitor_layout.model import LayoutError
from monitor_layout.profiles import layout_to_dict, layout_from_dict

def sink(kind="speaker", transport="usb"):
    return AudioSink(1, "physical.output", "External speaker", ["FL", "FR"], kind=kind, transport=transport)

def test_new_listener_and_projection_then_manual_independence():
    c = LayoutController()
    a = c.add_monitor(x=0)
    b = c.add_monitor(x=100, y=-100)
    assert c.layout.main() is None
    assert a.room_y == b.room_y == 0
    assert c.layout.listener_x == 80
    c.move_monitor(a.id, 10, 100)
    assert a.room_x == 40 and a.room_y == 0
    c.set_room_geometry(a.id, x=-60, y=-40, yaw=30)
    c.move_monitor(a.id, 200, 300)
    assert (a.room_x, a.room_y, a.room_yaw) == (-60, -40, 30)
    c.set_listener(x=4, y=100, facing=10)
    c.add_monitor(x=500)
    assert c.layout.listener_x == 4

def test_unknown_and_headphones_require_safe_classification():
    c = LayoutController()
    m = c.add_monitor()
    c.sinks = [sink("headphones")]
    with pytest.raises(LayoutError):
        c.add_peripheral(c.sinks[0].name, speaker_confirmed=True)
    with pytest.raises(LayoutError):
        c.map_device(m.id, c.sinks[0].name, speaker_confirmed=True)
    c.sinks = [sink("unknown", "bluetooth")]
    with pytest.raises(LayoutError):
        c.add_peripheral(c.sinks[0].name)
    p = c.add_peripheral(c.sinks[0].name, speaker_confirmed=True)
    assert p.speaker_count == 1  # never infer physical drivers from stereo channels
    assert c.layout.mappings[p.id].speaker_confirmed
    restored = layout_from_dict(layout_to_dict(c.layout))
    assert restored.mappings[p.id].speaker_confirmed

def test_standalone_export_disconnect_and_remove():
    c = LayoutController()
    c.sinks = [sink(transport="bluetooth")]
    p = c.add_peripheral(c.sinks[0].name)
    c.confirm_speaker_count(p.id, 1)
    c.confirm_channel_order(p.id, ["FL"])
    c.set_room_geometry(p.id, x=50, y=-50)
    text, warnings, errors = c.export_conf()
    assert not errors
    assert "combine.latency-compensate = true" in text
    assert "node.dont-fallback = true" in text
    assert "stream.dont-remix = true" in text
    assert any("Bluetooth" in w for w in warnings)
    c.sinks = []
    assert c.export_conf()[2]
    c.remove_target(p.id)
    assert not c.layout.targets() and not c.layout.mappings

def test_demo_repeated_discovery_never_runs_live_commands():
    c = LayoutController()
    c.load_demo(Path(__file__).resolve().parent.parent / "fixtures")
    def denied(*args, **kwargs):
        raise AssertionError("live discovery attempted")
    ids = [t.id for t in c.layout.targets()]
    for _ in range(2):
        assert c.discover(denied).source == "fixture"
        assert c.discover_sinks(denied) is None
    assert [t.id for t in c.layout.targets()] == ids
