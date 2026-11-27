"""Exercise the real hotkey script against fake WirePlumber output."""
from pathlib import Path
import os
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "toggle-audio.sh"
STATUS = """\
PipeWire
 └─ Clients:
        11. terminal
Audio
 ├─ Devices:
 │      62. ddHiFi USB
 ├─ Sinks:
 │  *  112. Unrelated speaker     [vol: 0.12]
 │      12. ddHiFi TC44Pro        [vol: 1.00]
 ├─ Sources:
 │      82. ddHiFi microphone    [vol: 1.00]
 ├─ Filters:
 │      90. dual-monitor filter
 │      91. dual_monitor_sink    [Audio/Sink]
 └─ Streams:
        40. test output
Video
 ├─ Sinks:
 │      70. unrelated ddHiFi camera
"""


@pytest.fixture
def fake_wpctl(tmp_path, monkeypatch):
    executable = tmp_path / "wpctl"
    executable.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$WPCTL_TEST_CALLS"
case "$1" in
  status) cat "$WPCTL_TEST_STATUS" ;;
  inspect)
    if [ "$2" != '@DEFAULT_AUDIO_SINK@' ] || [ -z "$WPCTL_TEST_DEFAULT" ]; then exit 1; fi
    printf 'id %s, type PipeWire:Interface:Node\\n' "$WPCTL_TEST_DEFAULT"
    ;;
  set-default) exit "$WPCTL_TEST_SET_EXIT" ;;
  *) exit 98 ;;
esac
"""
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("WPCTL_TEST_CALLS", str(tmp_path / "calls"))
    monkeypatch.setenv("WPCTL_TEST_STATUS", str(tmp_path / "status"))
    monkeypatch.setenv("WPCTL_TEST_SET_EXIT", "0")

    def run(current, status=STATUS, set_exit=0):
        (tmp_path / "status").write_text(status)
        monkeypatch.setenv("WPCTL_TEST_DEFAULT", current)
        monkeypatch.setenv("WPCTL_TEST_SET_EXIT", str(set_exit))
        script = os.environ.get("AUDIO_TOGGLE_SCRIPT", str(SCRIPT))
        result = subprocess.run(["bash", script], text=True, capture_output=True)
        return result, (tmp_path / "calls").read_text().splitlines()

    return run


@pytest.mark.parametrize(("current", "target"), [("12", "91"), ("91", "12"), ("112", "12")])
def test_switch_uses_exact_default_and_one_status_snapshot(fake_wpctl, current, target):
    result, calls = fake_wpctl(current)
    assert result.returncode == 0, result.stderr
    assert calls == ["status", "inspect @DEFAULT_AUDIO_SINK@", f"set-default {target}"]


def test_named_virtual_sink_is_found_when_listed_as_an_ordinary_sink(fake_wpctl):
    status = STATUS.replace(" │      91. dual_monitor_sink    [Audio/Sink]\n", "").replace(
        " ├─ Sources:", " │      91. dual_monitor_sink    [Audio/Sink]\n ├─ Sources:"
    )
    result, calls = fake_wpctl("12", status)
    assert result.returncode == 0, result.stderr
    assert calls[-1] == "set-default 91"


@pytest.mark.parametrize(
    "status",
    [STATUS.replace("dual_monitor_sink", "wrong-target"), STATUS.replace(
        " ├─ Sources:", " │      22. ddHiFi TC44Pro        [vol: 1.00]\n ├─ Sources:"
    )],
)
def test_refuses_to_guess_when_target_missing_or_ambiguous(fake_wpctl, status):
    result, calls = fake_wpctl("12", status)
    assert result.returncode == 1
    assert calls == ["status"]


def test_refuses_to_switch_when_current_default_is_unknown(fake_wpctl):
    result, calls = fake_wpctl("")
    assert result.returncode == 1
    assert calls == ["status", "inspect @DEFAULT_AUDIO_SINK@"]


def test_wireplumber_switch_errors_reach_caller(fake_wpctl):
    result, calls = fake_wpctl("12", set_exit=23)
    assert result.returncode == 23
    assert calls[-1] == "set-default 91"
