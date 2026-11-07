"""Fixture-only native snapshots. Run: python3 scripts/capture_gui.py."""
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
import os

STATES = ("front", "room", "audio", "preview", "minimum", "empty", "mapping")


def _is_isolated_capture(env):
    runtime = env.get("XDG_RUNTIME_DIR", "")
    path = Path(runtime)
    return (env.get("PW_GUI_TESTS") == "1"
            and env.get("GDK_BACKEND") == "broadway"
            and bool(runtime)
            and env.get("PW_GUI_TEST_RUNTIME") == runtime
            and path.is_absolute()
            and path.name == "runtime"
            and path.parent.name.startswith("monitor-layout-tests-"))

@pytest.mark.parametrize('state', STATES)
def test_capture(state):
    if not _is_isolated_capture(os.environ):
        pytest.skip("Capture requires scripts/run_gui_tests.py's private Broadway session")
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib
    import cairo
    from monitor_layout.gui import MonitorLayoutApp
    from monitor_layout.controller import LayoutController
    from monitor_layout.model import Peripheral

    view = 'room' if state == 'room' else 'front'
    ctl = LayoutController()
    ctl.load_demo(Path('fixtures'))
    left, right = ctl.layout.monitors
    if view == 'front':
        ctl.set_orientation(right.id, 'portrait')
        ctl.move_monitor(right.id, 65, -8)
        ctl.set_main(right.id)
    else:
        for monitor, x, yaw in zip(ctl.layout.monitors, [-34, 34], [20, -20]):
            ctl.set_room_geometry(monitor.id, x=x, y=-70, yaw=yaw)
        ctl.set_listener(x=0, y=0, facing=0)
        ctl.layout.peripherals.append(Peripheral('speaker-demo', 'Bluetooth speaker', room_x=85,
            room_y=12, room_yaw=-25, speaker_confirmed=True, room_confirmed=True))
    if state in ('audio', 'preview', 'mapping'):
        for monitor, sink in zip(ctl.layout.monitors, ctl.sinks):
            ctl.map_device(monitor.id, sink.name, speaker_confirmed=True)
    if state == 'empty':
        ctl.layout.monitors.clear()
    app = MonitorLayoutApp(demo=True, controller=ctl, initial_view=view,
        application_id='org.variablethe.MonitorLayout.Capture'+state)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    if state in ('audio', 'preview', 'mapping'):
        window.inspector_stack.set_visible_child_name('outputs' if state in ('audio', 'mapping') else 'preview')
    if state == 'mapping':
        from gi.repository import Adw
        for row in window._output_rows:
            if isinstance(row, Adw.ExpanderRow):
                row.set_expanded(True)
                break
    if state == 'minimum':
        window.set_default_size(720, 520)
    if view == 'front' and state != 'empty':
        window.canvas.select(right.id)
    elif view == 'room':
        window._select_room('__listener__')
    try:
        context = GLib.MainContext.default()
        for _ in range(100):
            while context.pending():
                context.iteration(False)
            time.sleep(.01)
        width, height = window.get_width(), window.get_height()
        assert width >= 720 and height >= 520
        snapshot = Gtk.Snapshot.new()
        snapshot.render_background(window.get_style_context(), 0, 0, width, height)
        type(window).do_snapshot(window, snapshot)
        node = snapshot.to_node()
        assert node is not None
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        node.draw(cairo.Context(surface))
        path = Path('docs/screenshots') / ('monitor-layout-' + state + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        surface.write_to_png(str(path))
        print(path.resolve(), width, height)
    finally:
        window.destroy()
        app.quit()


if __name__ == "__main__":
    # Broadway can cache stale styles across separate applications in one
    # session. Capture each real widget tree in a fresh isolated display.
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", choices=STATES, help="Capture only this state")
    args = parser.parse_args()
    for state in (args.state,) if args.state else STATES:
        subprocess.run([sys.executable, str(root / "scripts/run_gui_tests.py"),
                        f"scripts/capture_gui.py::test_capture[{state}]", "-q"],
                       cwd=root, check=True)
