"""The capture helper must stay inert outside the private GUI runner."""
import importlib.util
from pathlib import Path


def load_helper():
    path = Path(__file__).resolve().parents[1] / "scripts" / "capture_gui.py"
    spec = importlib.util.spec_from_file_location("capture_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_isolation_guard():
    helper = load_helper()
    runtime = "/tmp/monitor-layout-tests-example/runtime"
    valid = dict(PW_GUI_TESTS="1", GDK_BACKEND="broadway",
                 XDG_RUNTIME_DIR=runtime, PW_GUI_TEST_RUNTIME=runtime)
    assert helper._is_isolated_capture(valid)
    assert not helper._is_isolated_capture({})
    for key, value in (("PW_GUI_TESTS", "0"), ("GDK_BACKEND", "wayland"),
                       ("PW_GUI_TEST_RUNTIME", "/run/user/1000"),
                       ("XDG_RUNTIME_DIR", "/run/user/1000")):
        assert not helper._is_isolated_capture({**valid, key: value})
    assert not helper._is_isolated_capture({**valid,
        "XDG_RUNTIME_DIR": "/run/user/1000", "PW_GUI_TEST_RUNTIME": "/run/user/1000"})


def test_capture_skips_before_gtk_import(monkeypatch):
    import builtins
    import pytest
    helper = load_helper()
    monkeypatch.delenv("PW_GUI_TESTS", raising=False)
    original = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name not in ("gi", "cairo", "monitor_layout.gui")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(pytest.skip.Exception):
        helper.test_capture("front")
