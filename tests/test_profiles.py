import json
from pathlib import Path

import pytest

from monitor_layout.model import Layout, LayoutError, Monitor
from monitor_layout.profiles import load_profile, save_profile


def demo_layout():
    lay = Layout(name="Desk")
    lay.add_monitor(Monitor(id="monitor-1", name="L", x=0, y=0, width=60, height=30))
    lay.add_monitor(Monitor(id="monitor-2", name="R", x=64, y=0, width=60, height=30))
    lay.set_main("monitor-1")
    lay.map_device("monitor-1", "alsa_output.dev.a", 2)
    return lay


def test_roundtrip(tmp_path):
    lay = demo_layout()
    path = tmp_path / "desk.json"
    save_profile(lay, path)
    loaded = load_profile(path)
    assert loaded.name == "Desk"
    assert [m.id for m in loaded.monitors] == ["monitor-1", "monitor-2"]
    assert loaded.main().id == "monitor-1"
    assert loaded.mappings["monitor-1"].device == "alsa_output.dev.a"


def test_save_does_not_leave_tmp_files(tmp_path):
    save_profile(demo_layout(), tmp_path / "desk.json")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_load_missing_file(tmp_path):
    with pytest.raises(LayoutError, match="not found"):
        load_profile(tmp_path / "nope.json")


def test_reject_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(LayoutError):
        load_profile(p)


def test_reject_wrong_schema(tmp_path):
    p = tmp_path / "future.json"
    p.write_text(json.dumps({"schema": 99, "monitors": []}))
    with pytest.raises(LayoutError, match="schema"):
        load_profile(p)


def test_reject_non_finite_values(tmp_path):
    lay = demo_layout()
    p = tmp_path / "nan.json"
    # json.dumps(allow_nan=False) refuses to even write NaN
    lay.monitors[0].x = float("nan")
    with pytest.raises((LayoutError, ValueError)):  # validate() or dumps
        save_profile(lay, p)
    # and a hand-crafted NaN profile refuses to load
    lay.monitors[0].x = 0.0
    raw = json.dumps({"schema": 1, "monitors": []}).replace(
        '"monitors": []',
        '"monitors": [{"id":"a","name":"a","x":NaN,"y":0,"width":60,"height":30}]',
    )
    p.write_text(raw)
    with pytest.raises(LayoutError):
        load_profile(p)


def test_reject_duplicate_devices_on_load(tmp_path):
    from monitor_layout.profiles import layout_to_dict

    lay = demo_layout()
    lay.map_device("monitor-2", "alsa_output.dev.b", 2)
    payload = layout_to_dict(lay)
    payload["mappings"]["monitor-2"]["device"] = "alsa_output.dev.a"
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(LayoutError, match="mapped to both"):
        load_profile(p)


def test_reject_orphan_mapping(tmp_path):
    from monitor_layout.profiles import layout_to_dict

    payload = layout_to_dict(demo_layout())
    payload["mappings"]["ghost"] = {"device": "x", "confirmed_speakers": 2}
    p = tmp_path / "ghost.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(LayoutError, match="unknown monitor"):
        load_profile(p)


def test_no_audio_side_effects_on_save(tmp_path, monkeypatch):
    """Saving a profile must not touch pipewire config or run any command."""
    calls = []
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: calls.append(a) or None, raising=True
    )
    save_profile(demo_layout(), tmp_path / "desk.json")
    assert calls == []
    assert not (tmp_path / "pipewire").exists()
