"""Room geometry is explicit, persistent, and independent of front elevation."""
import copy
import math
import random

import pytest

from monitor_layout.model import DeviceMapping, Layout, LayoutError, Monitor, Peripheral
from monitor_layout.panning import compute_contributions
from monitor_layout.profiles import layout_from_dict, layout_to_dict


def room():
    layout = Layout(reference_mode="listener", listener_x=0, listener_y=70,
                    listener_confirmed=True)
    layout.add_monitor(Monitor("left", "Left", x=-70, room_x=-40, room_y=0,
                               room_yaw=20, room_confirmed=True))
    layout.add_monitor(Monitor("right", "Right", x=10, room_x=40, room_y=0,
                               room_yaw=-20, room_confirmed=True))
    return layout


def bounded(result):
    for row in result.matrix.values():
        assert sum(row.values()) <= 1 + 1e-9
        assert all(0 <= gain <= 1 for gain in row.values())
        assert sum(row.values()) > 0, "No input channel may be silently dropped"
    for key in result.speaker_keys:
        assert result.speaker_total(key) <= 1 + 1e-9


def test_listener_no_main_roundtrip_and_optional_main():
    layout = room()
    assert layout.main() is None
    layout.validate()
    original = compute_contributions(layout).matrix
    layout.set_main("left")
    assert compute_contributions(layout).matrix == original
    layout.set_main("right")
    assert compute_contributions(layout).matrix == original
    layout.clear_main()
    restored = layout_from_dict(layout_to_dict(layout))
    assert restored == layout
    assert restored.main() is None
    assert compute_contributions(restored).matrix == original


def test_front_stack_move_cannot_make_rear_speakers():
    layout = room()
    original = compute_contributions(layout, "5.1").matrix
    layout.monitors[0].y = -500
    layout.monitors[0].x = 400
    layout.project_approximate_room()
    assert compute_contributions(layout, "5.1").matrix == original
    assert all(abs(angle) < 90 for angle in layout.speaker_azimuths())


def test_unconfirmed_projection_changes_x_not_depth_and_preserves_manual_seat():
    layout = Layout(reference_mode="listener")
    mon = Monitor("one", "One", x=-80, y=-100, width=60)
    layout.add_monitor(mon)
    layout.project_approximate_room()
    assert (mon.room_x, mon.room_y) == (-50, 0)
    assert (layout.listener_x, layout.listener_y) == (-50, 70)
    layout.listener_confirmed = True
    layout.listener_x = 17
    mon.x = 20
    mon.y = 150
    layout.project_approximate_room()
    assert (mon.room_x, mon.room_y) == (50, 0)
    assert layout.listener_x == 17
    mon.room_confirmed = True
    mon.room_x, mon.room_y, mon.room_yaw = (8, -19, 45)
    mon.x = -300
    layout.project_approximate_room()
    assert (mon.room_x, mon.room_y, mon.room_yaw) == (8, -19, 45)


@pytest.mark.parametrize("schema", [1, 2])
def test_legacy_migration_preserves_front_and_panning(schema):
    legacy = Layout()
    legacy.add_monitor(Monitor("one", "One", x=-30, y=-400, is_main=True))
    legacy.add_monitor(Monitor("two", "Two", x=40, y=20))
    legacy.map_device("one", "alsa_output.one", 2)
    legacy.mappings["one"].channel_order = ["FR", "FL"]
    before = compute_contributions(legacy, "5.1").matrix
    data = layout_to_dict(legacy)
    data["schema"] = schema
    for field in ("reference_mode", "peripherals", "listener_x", "listener_y",
                  "listener_facing", "listener_confirmed"):
        data.pop(field)
    for mon in data["monitors"]:
        for field in ("room_x", "room_y", "room_yaw", "room_confirmed"):
            mon.pop(field)
    result = layout_from_dict(data)
    assert result.reference_mode == "main"
    assert [(m.x, m.y) for m in result.monitors] == [(-30, -400), (40, 20)]
    assert all(m.room_y == 0 and not m.room_confirmed for m in result.monitors)
    assert compute_contributions(result, "5.1").matrix == before
    assert result.mappings["one"].channel_order == (["FR", "FL"] if schema == 2 else [])


def test_standalone_only_roundtrip_order_and_removal():
    layout = Layout(reference_mode="listener")
    speaker = Peripheral("speaker-1", "USB pair", room_x=30, room_y=-20,
                         room_yaw=35, speaker_count=2, speaker_confirmed=True,
                         room_confirmed=True)
    layout.add_peripheral(speaker)
    layout.map_device(speaker.id, "alsa_output.usb-speakers", 2)
    layout.mappings[speaker.id].channel_order = ["FR", "FL"]
    restored = layout_from_dict(layout_to_dict(layout))
    assert restored == layout
    bounded(compute_contributions(restored, "5.1"))
    restored.remove_peripheral(speaker.id)
    assert not restored.targets() and not restored.mappings


def test_stable_slots_through_move_yaw_orientation_order():
    layout = room()
    layout.add_peripheral(Peripheral("speaker-1", "Soundbar", speaker_count=4))
    expected = {s.key: (s.monitor_id, s.slot) for s in layout.speakers()}
    layout.monitors[0].orientation = "portrait"
    layout.monitors.reverse()
    for target in layout.targets():
        target.room_x += 150
        target.room_y -= 30
        target.room_yaw += 170
    actual = {s.key: (s.monitor_id, s.slot) for s in layout.speakers()}
    assert expected == actual
    layout.mappings["speaker-1"] = DeviceMapping("alsa_output.bar", 4,
                                                ["AUX3", "AUX1", "AUX0", "AUX2"])
    layout.mappings["speaker-1"].validate_channel_order(4, ["AUX0", "AUX1", "AUX2", "AUX3"])
    with pytest.raises(LayoutError):
        layout.mappings["speaker-1"].validate_channel_order(3, ["AUX0", "AUX1", "AUX2", "AUX3"])


def test_mirror_room_swaps_stereo_and_surround_channels():
    layout = Layout(reference_mode="listener", listener_x=15, listener_y=40,
                    listener_facing=25)
    for i, (x, y) in enumerate([(-30, -50), (80, -10), (-10, 100), (45, 90)]):
        layout.add_peripheral(Peripheral(str(i), str(i), room_x=x, room_y=y))
    mirror = copy.deepcopy(layout)
    mirror.listener_x *= -1
    mirror.listener_facing *= -1
    for target in mirror.targets():
        target.room_x *= -1
        target.room_yaw *= -1
    for mode in ("stereo", "5.1"):
        a, b = compute_contributions(layout, mode), compute_contributions(mirror, mode)
        for ch, flipped in (("FL", "FR"), ("FR", "FL"), ("FC", "FC"),
                            ("RL", "RR"), ("RR", "RL"), ("LFE", "LFE")):
            if ch in a.channels:
                assert a.matrix[ch] == b.matrix[flipped]
        bounded(a)
        bounded(b)


def test_rotation_of_entire_room_preserves_distribution():
    layout = room()
    a = compute_contributions(layout, "5.1").matrix
    theta = math.radians(65)
    for target in layout.targets():
        x, y = target.room_x, target.room_y
        target.room_x = x * math.cos(theta) - y * math.sin(theta)
        target.room_y = x * math.sin(theta) + y * math.cos(theta)
        target.room_yaw += 65
    x, y = layout.listener_x, layout.listener_y
    layout.listener_x = x * math.cos(theta) - y * math.sin(theta)
    layout.listener_y = x * math.sin(theta) + y * math.cos(theta)
    layout.listener_facing += 65
    assert a == compute_contributions(layout, "5.1").matrix


def test_moving_seat_or_speaker_changes_gains():
    layout = room()
    a = compute_contributions(layout).matrix
    layout.listener_x += 20
    b = compute_contributions(layout).matrix
    assert a != b
    layout.peripherals.append(Peripheral("rear", "Rear", room_x=-60, room_y=140))
    c = compute_contributions(layout).matrix
    layout.peripherals[0].room_x = 60
    assert c != compute_contributions(layout).matrix
    layout.listener_facing = 180
    assert b != compute_contributions(layout).matrix


def test_stereo_rear_speakers_receive_bounded_widening_not_surround():
    layout = room()
    layout.add_peripheral(Peripheral("rear", "Bluetooth speaker", room_x=0, room_y=170))
    result = compute_contributions(layout)
    assert result.channels == ["FL", "FR"]
    assert all(result.matrix[ch]["rear/C"] > 0 for ch in result.channels)
    assert any("not discrete surround" in w for w in result.warnings)
    bounded(result)


def test_near_perpendicular_51_channels_survive_rounding():
    layout = Layout(reference_mode="listener", listener_y=0)
    layout.add_peripheral(Peripheral("one", "one", room_x=0.001, room_y=100))
    bounded(compute_contributions(layout, "5.1"))


def test_randomized_room_headroom_and_no_channel_loss_after_rounding():
    rng = random.Random(43)
    for _ in range(100):
        layout = Layout(reference_mode="listener", listener_x=rng.uniform(-50, 50),
                        listener_y=rng.uniform(-50, 50), listener_facing=rng.uniform(-180, 180))
        for i in range(rng.randint(1, 16)):
            layout.add_peripheral(Peripheral(str(i), str(i),
                room_x=rng.uniform(-300, 300), room_y=rng.uniform(-300, 300),
                room_yaw=rng.uniform(-180, 180), speaker_count=rng.randint(1, 4)))
        bounded(compute_contributions(layout))
        bounded(compute_contributions(layout, "5.1"))


@pytest.mark.parametrize("field,value", [("room_x", float("nan")), ("room_y", float("inf")),
                                         ("room_yaw", 40000), ("room_confirmed", "yes")])
def test_reject_invalid_room_state(field, value):
    layout = room()
    setattr(layout.monitors[0], field, value)
    with pytest.raises(LayoutError):
        layout.validate()


def test_reject_duplicate_target_id_and_multiple_optional_mains():
    layout = room()
    with pytest.raises(LayoutError):
        layout.add_peripheral(Peripheral("left", "duplicate"))
    layout.monitors[0].is_main = layout.monitors[1].is_main = True
    with pytest.raises(LayoutError):
        layout.validate()


def test_mapping_speaker_confirmation_persists_but_is_not_inferred_for_legacy():
    layout = room()
    layout.mappings["left"] = DeviceMapping("alsa_output.unknown", 2, ["FL", "FR"], True)
    data = layout_to_dict(layout)
    assert layout_from_dict(data).mappings["left"].speaker_confirmed is True
    data["schema"] = 2
    data["monitors"][0]["is_main"] = True
    assert layout_from_dict(data).mappings["left"].speaker_confirmed is False
