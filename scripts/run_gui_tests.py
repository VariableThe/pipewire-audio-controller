#!/usr/bin/env python3
"""Run GTK tests on a private Broadway display and D-Bus session, never the desktop."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent

def main():
    daemon = shutil.which("gtk4-broadwayd")
    bus = shutil.which("dbus-run-session")
    if not daemon or not bus:
        print("GUI tests require gtk4-broadwayd and dbus-run-session", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="monitor-layout-tests-") as tmp:
        env = os.environ.copy()
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "HYPRLAND_INSTANCE_SIGNATURE",
                    "SWAYSOCK", "I3SOCK", "PULSE_SERVER", "PIPEWIRE_REMOTE", "PIPEWIRE_RUNTIME_DIR"):
            env.pop(key, None)
        for key, suffix in (("XDG_RUNTIME_DIR", "runtime"), ("XDG_CONFIG_HOME", "config"),
                            ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
            path = Path(tmp) / suffix
            path.mkdir(mode=0o700)
            env[key] = str(path)
        env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=":1", PW_GUI_TESTS="1",
                   PW_GUI_TEST_RUNTIME=env["XDG_RUNTIME_DIR"], GSETTINGS_BACKEND="memory",
                   GTK_A11Y="none", NO_AT_BRIDGE="1", ADW_DISABLE_PORTAL="1", GTK_USE_PORTAL="0", GIO_USE_VFS="local")
        with open(Path(tmp) / "broadway.log", "w+") as log:
            proc = subprocess.Popen([daemon, "-a", "127.0.0.1", "-p", "0", ":1"],
                                    env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 8
                while not list(Path(env["XDG_RUNTIME_DIR"]).glob("broadway*.socket")):
                    if proc.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        print(log.read(), file=sys.stderr)
                        return 2
                    time.sleep(0.05)
                args = sys.argv[1:] or ["tests/test_gui.py", "tests/test_room_canvas_gui.py", "-q"]
                return subprocess.run([bus, "--", sys.executable, "-m", "pytest", *args],
                                      cwd=ROOT, env=env).returncode
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

if __name__ == "__main__":
    raise SystemExit(main())
