import subprocess

import pytest
from conftest import fixture_text

from monitor_layout import detect
from monitor_layout.model import LayoutError


def test_hyprctl_dual_fixture_physical_size():
    res = detect.parse_hyprctl_monitors(fixture_text("hyprctl-dual.json"))
    assert res.source == "hyprctl"
    assert not res.approximate
    mons = res.monitors
    assert len(mons) == 2
    left, right = mons
    assert all(m.physical_known for m in mons)
    assert left.width_cm == pytest.approx(59.7, abs=0.02)
    assert left.orientation == "landscape" == right.orientation
    # consistent geometry: side-by-side panels remain exactly adjacent
    assert right.x_cm == pytest.approx(left.width_cm, abs=0.02)


def test_hyprctl_mixed_fixture_portrait_fallback_and_consistency():
    res = detect.parse_hyprctl_monitors(fixture_text("hyprctl-mixed.json"))
    mons = res.monitors
    assert len(mons) == 3
    assert mons[1].orientation == "portrait"
    # eDP-1 reports no physical size -> whole result flagged approximate
    assert mons[2].physical_known is False
    assert res.approximate
    # one global scale: the side panel starts exactly where the main ends
    assert mons[1].x_cm == pytest.approx(mons[0].width_cm, abs=0.02)
    assert any("approximate" in w or "mixed" in w.lower() for w in res.warnings)


def test_hyprctl_rejects_malformed():
    with pytest.raises(LayoutError):
        detect.parse_hyprctl_monitors("not json")
    with pytest.raises(LayoutError):
        detect.parse_hyprctl_monitors("{}")
    with pytest.raises(LayoutError):
        detect.parse_hyprctl_monitors('[{"name": "X"}]')
    with pytest.raises(LayoutError):
        detect.parse_hyprctl_monitors(
            '[{"name":"X","width":0,"height":10,"x":0,"y":0}]'
        )
    with pytest.raises(LayoutError):
        detect.parse_hyprctl_monitors(
            '[{"name":"X","width":10,"height":10,"x":0,"y":0,"transform":"rotated"}]'
        )


def test_wlr_randr_fixture_mixed_dpi_rotation():
    res = detect.parse_wlr_randr(fixture_text("wlr-randr-single.txt"))
    assert res.source == "wlr-randr"
    mons = res.monitors
    assert [m.name for m in mons] == ["HDMI-A-1", "DP-1"]  # disabled skipped
    assert mons[1].orientation == "portrait"
    # portrait + landscape with different density: scale disagreement warned
    assert any("median" in w for w in res.warnings)
    # ...but geometry stays consistent: the right panel starts at the left
    # panel's right edge, no tearing
    assert mons[1].x_cm == pytest.approx(mons[0].width_cm, abs=0.02)


def test_detect_falls_back_and_gracefully_reports_missing_tools():
    def runner(argv):
        raise FileNotFoundError(2, "no such file", argv[0])

    result = detect.detect_monitors(runner=runner)
    assert result.source == "none"
    assert result.monitors == []
    assert result.error and "manually" in result.error


def test_detect_uses_hyprctl_first():
    calls = []

    def runner(argv):
        calls.append(argv[0])
        return fixture_text("hyprctl-dual.json")

    result = detect.detect_monitors(runner=runner)
    assert result.source == "hyprctl"
    assert len(result.monitors) == 2
    assert calls == ["hyprctl"]


def test_detect_falls_back_to_wlr_randr_on_timeout():
    def runner(argv):
        if argv[0] == "hyprctl":
            raise subprocess.TimeoutExpired(argv, 5)
        return fixture_text("wlr-randr-single.txt")

    result = detect.detect_monitors(runner=runner)
    assert result.source == "wlr-randr"
    assert len(result.monitors) == 2


def test_detected_to_monitor_round_trip():
    dm = detect.parse_hyprctl_monitors(fixture_text("hyprctl-dual.json")).monitors[0]
    mon = detect.detected_to_monitor(dm, "monitor-1")
    mon.validate()
    assert mon.orientation == "landscape"
    assert mon.width == pytest.approx(59.7, abs=0.05)
