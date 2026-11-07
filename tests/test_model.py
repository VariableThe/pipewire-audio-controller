import math

import pytest

from monitor_layout.model import (
    Layout,
    LayoutError,
    Monitor,
    Rect,
)


def mon(id="monitor-1", **kw):
    kw.setdefault("name", id)
    return Monitor(id=id, **kw)


def test_default_layout_builds_and_has_main():
    lay = Layout()
    lay.add_monitor(mon("monitor-1"))
    lay.add_monitor(mon("monitor-2", x=70.0))
    assert lay.main() is not None
    assert lay.main().id == "monitor-1"  # first added becomes main
    lay.set_main("monitor-2")
    assert lay.main().id == "monitor-2"
    assert all(not m.is_main or m.id == "monitor-2" for m in lay.monitors)


def test_portrait_rect_swaps_dimensions():
    m = mon(width=60.0, height=34.0, orientation="portrait")
    r = m.rect()
    assert (r.width, r.height) == (34.0, 60.0)


def test_speaker_points_bottom_edge_lr_order():
    m = mon(x=0.0, y=0.0, width=60.0, height=30.0, speaker_count=2)
    pts = m.speaker_points()
    assert len(pts) == 2
    assert pts[0][0] < pts[1][0]  # left point first
    assert pts[0][1] > 30.0  # standoff pushes beyond bottom edge


def test_speaker_points_single_centered():
    m = mon(width=60.0, height=30.0, speaker_count=1)
    (px, py), = m.speaker_points()
    assert px == pytest.approx(30.0)


def test_reject_non_finite_and_negative_geometry():
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(LayoutError):
            mon(width=bad).validate()
    with pytest.raises(LayoutError):
        mon(width=-10.0).validate()
    with pytest.raises(LayoutError):
        mon(width=0.0).validate()
    with pytest.raises(LayoutError):
        mon(x=float("nan")).validate()


def test_reject_bad_enums_and_types():
    with pytest.raises(LayoutError):
        mon(orientation="diag").validate()
    with pytest.raises(LayoutError):
        mon(speaker_edge="corner").validate()
    with pytest.raises(LayoutError):
        mon(speaker_count=0).validate()
    with pytest.raises(LayoutError):
        mon(speaker_count=17).validate()
    with pytest.raises(LayoutError):
        mon(speaker_count=True).validate()
    with pytest.raises(LayoutError):
        mon(is_main="yes").validate()
    with pytest.raises(LayoutError):
        mon(name="").validate()


def test_duplicate_ids_rejected():
    lay = Layout()
    lay.add_monitor(mon("monitor-1"))
    with pytest.raises(LayoutError):
        lay.add_monitor(mon("monitor-1"))


def test_duplicate_device_mapping_rejected():
    lay = Layout()
    lay.add_monitor(mon("monitor-1"))
    lay.add_monitor(mon("monitor-2", x=70.0))
    lay.map_device("monitor-1", "alsa_out.dev1", 2)
    with pytest.raises(LayoutError):
        lay.map_device("monitor-2", "alsa_out.dev1", 2)
    # re-assigning the same monitor is fine
    lay.map_device("monitor-1", "alsa_out.dev2", 2)
    lay.mappings["monitor-1"].validate()


def test_validate_requires_exactly_one_main():
    lay = Layout()
    lay.add_monitor(mon("monitor-1"))
    lay.add_monitor(mon("monitor-2", x=70.0))
    for m in lay.monitors:
        m.is_main = False
    with pytest.raises(LayoutError):
        lay.validate()
    for m in lay.monitors:
        m.is_main = True
    with pytest.raises(LayoutError):
        lay.validate()


def test_remove_monitor_reassigns_main():
    lay = Layout()
    lay.add_monitor(mon("monitor-1"))
    lay.add_monitor(mon("monitor-2", x=70.0))
    lay.set_main("monitor-1")
    lay.remove_monitor("monitor-1")
    assert lay.main().id == "monitor-2"
    with pytest.raises(LayoutError):
        lay.remove_monitor("ghost")


def test_overlap_detection():
    lay = Layout()
    lay.add_monitor(mon("monitor-1", x=0, y=0, width=60, height=30))
    lay.add_monitor(mon("monitor-2", x=70, y=0, width=60, height=30))
    assert lay.overlaps() == []
    lay.monitors[1].x = 30.0
    assert lay.overlaps() == [("monitor-1", "monitor-2")]


def test_speaker_roles_left_to_right_from_listener():
    lay = Layout()
    lay.add_monitor(mon("monitor-1", x=0, y=0, width=60, height=30))
    lay.add_monitor(mon("monitor-2", x=70, y=0, width=60, height=30))
    speakers = lay.speakers()
    assert len(speakers) == 4
    # main = monitor-1 at left; angles increase left->right
    assert [s.key for s in speakers] == [
        "monitor-1/L",
        "monitor-1/R",
        "monitor-2/L",
        "monitor-2/R",
    ]
    xs = [s.x for s in speakers]
    assert xs == sorted(xs)


def test_reference_point_requires_main():
    lay = Layout()
    with pytest.raises(LayoutError):
        lay.reference_point()


def test_bounds_and_normalize():
    lay = Layout()
    lay.add_monitor(mon("monitor-1", x=-50, y=-30, width=60, height=30))
    lay.normalize(margin_cm=4.0)
    b = lay.bounds()
    assert (b.x, b.y) == (pytest.approx(4.0), pytest.approx(4.0))


def test_monitor_limit():
    lay = Layout()
    for i in range(16):
        lay.add_monitor(mon(f"monitor-{i}", x=i * 70))
    with pytest.raises(LayoutError):
        lay.add_monitor(mon("monitor-overflow", x=2000))
