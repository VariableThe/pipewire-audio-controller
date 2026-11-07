import os
import pytest
if os.environ.get("PW_GUI_TESTS") != "1":
    pytest.skip("Run GUI tests with python3 scripts/run_gui_tests.py", allow_module_level=True)
pytest.importorskip("gi")
import gi
import uuid

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk

from monitor_layout.canvas import LayoutCanvas
from monitor_layout.controller import LayoutController
from monitor_layout.gui import MonitorLayoutApp, MonitorLayoutWindow

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def _find_canvas(widget):
    if isinstance(widget, LayoutCanvas):
        return widget
    candidate = None
    if hasattr(widget, "get_child"):
        child = widget.get_child()
        if child is not None:
            candidate = _find_canvas(child)
    if candidate is None and hasattr(widget, "get_first_child"):
        child = widget.get_first_child()
        while child is not None:
            candidate = _find_canvas(child)
            if candidate is not None:
                break
            child = child.get_next_sibling()
    return candidate


def test_gui_startup_attaches_canvas_and_selects_main(broadway_display):  # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    canvas = _find_canvas(window)
    assert canvas is not None, "LayoutCanvas was not attached to the window"
    assert canvas.get_parent() is not None, "canvas was instantiated but never attached"
    assert canvas.selected_id == "monitor-1"
    assert len(controller.layout.monitors) == 2
    # Rendering from the theme must not fail on draw.
    canvas.scene()
    window.destroy()
    app.quit()


def test_gui_attached_canvas_draw_scene_after_style_context(broadway_display):  # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    canvas = _find_canvas(window)
    assert canvas is not None
    ctx = canvas.get_style_context()
    assert ctx is not None
    scene = canvas.scene()
    assert len(scene.layout.monitors) == 2
    window.destroy()
    app.quit()


def test_gui_canvas_selection_drag_and_keys(broadway_display): # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    # Give the drawing area an explicit allocation basis.
    canvas = _find_canvas(window)
    assert canvas is not None
    canvas.set_size_request(720, 460)
    canvas.get_width = lambda: 720
    canvas.get_height = lambda: 460
    canvas.scene().compute_transform(720, 460)

    second = controller.layout.monitors[1]
    scene = canvas.scene()
    scene.compute_transform(720, 460)
    cx, cy = scene.to_px(second.rect().x + second.rect().width / 2,
                          second.rect().y + second.rect().height / 2)
    canvas._on_click(None, 1, cx, cy)
    assert canvas.selected_id == second.id

    before_x, before_y = second.x, second.y
    sx, sy = scene.to_px(second.rect().x + 4.0, second.rect().y + 4.0)
    canvas._on_drag_begin(None, sx, sy)
    assert canvas._drag_id == second.id
    # Translate 5 cm to the right at current scale (arithmetic in world coords).
    dx = 5.0 * scene.scale
    canvas._on_drag_update(None, dx, 0.0)
    assert (second.x, second.y) != (before_x, before_y)
    assert second.x == pytest.approx(round((before_x + 5.0) * 2) / 2)
    canvas._on_drag_update(None, -dx, 0.0)
    assert second.x == pytest.approx(round((before_x - 5.0) * 2) / 2)
    canvas._on_drag_end(None, dx, 0.0)
    assert canvas._drag_id is None

    # Rotation and main-display keyboard flows.
    assert second.orientation == "landscape"
    assert canvas.key_pressed(Gdk.KEY_r, 0) is True
    assert controller.layout.get(second.id).orientation == "portrait"
    assert controller.layout.main() is None
    assert canvas.key_pressed(Gdk.KEY_m, 0) is True
    assert controller.layout.main().id == second.id

    window.destroy()
    app.quit()


def test_gui_save_reload_roundtrip(broadway_display, tmp_path):  # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    layout_path = tmp_path / "monitor-layout.json"
    window.save_to(layout_path)
    assert layout_path.exists()
    before_numbers = controller.monitor_numbers()
    window.load_from(layout_path)
    assert controller.layout.main() is None
    assert controller.layout.reference_mode == "listener"
    assert controller.monitor_numbers() == before_numbers
    assert window.canvas.selected_id is None

    # Exporting requires at least one mapped monitor, so use bundled sinks.
    assert controller.sinks
    controller.map_device("monitor-1", controller.sinks[0].name, speaker_confirmed=True)
    if len(controller.sinks) > 1:
        controller.map_device("monitor-2", controller.sinks[1].name, speaker_confirmed=True)

    for mid, mapping in controller.layout.mappings.items():
        controller.confirm_speaker_count(mid, controller.layout.get(mid).speaker_count)
        controller.confirm_channel_order(mid, controller.device_channels(mapping.device)[:2])

    export_path = tmp_path / "monitor-layout.conf"
    ok, warnings = window.export_to(export_path)
    assert ok is True, warnings
    assert any("approximate" in warning for warning in warnings)
    text = export_path.read_text(encoding="utf-8")
    assert "filter-chain" in text
    assert "node.name" in text

    window.destroy()
    app.quit()


def test_gui_dirty_state_tracks_persistent_edits(broadway_display, tmp_path):  # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    canvas = _find_canvas(window)
    assert canvas is not None
    canvas.get_width = lambda: 720
    canvas.get_height = lambda: 460
    canvas.set_size_request(720, 460)

    assert window._dirty is False
    # Selection only must not create an unsaved edit.
    scene = canvas.scene()
    mon = controller.layout.monitors[1]
    scene.compute_transform(720, 460)
    cx, cy = scene.to_px(mon.rect().x + mon.rect().width / 2,
                         mon.rect().y + mon.rect().height / 2)
    canvas._on_click(None, 1, cx, cy)
    assert window._dirty is False

    # Persistent layout edits become dirty; save clears it.
    controller.move_monitor(mon.id, mon.x + 2.0, mon.y)
    assert window._dirty is True
    window.save_to(tmp_path / "layout.json")
    assert window._dirty is False
    window.load_from(tmp_path / "layout.json")
    assert window._dirty is False
    assert canvas.selected_id is None

    # Close handling: unsaved edits require confirmation, forced close proceeds.
    controller.rotate_monitor(controller.layout.monitors[0].id)
    assert window._dirty is True
    assert window._on_close_request(window) is True
    window._force_close = True
    assert window._on_close_request(window) is False

    window.destroy()
    app.quit()


def test_gui_add_and_remove_displays(broadway_display):  # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    canvas = _find_canvas(window)
    assert canvas is not None

    initial = len(controller.layout.monitors)
    started_dirty = window._dirty
    window.on_add_monitor()
    assert len(controller.layout.monitors) == initial + 1
    assert canvas.selected_id is not None

    window.on_remove()
    assert len(controller.layout.monitors) == initial
    assert canvas.selected_id is None
    assert window._dirty is True or window._dirty is started_dirty

    window.destroy()
    app.quit()


def test_gui_cancel_close_keeps_window(broadway_display, tmp_path):  # noqa: ARG001
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                                application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    controller.move_monitor(controller.layout.monitors[0].id, 1.0, 0.0)
    assert window._dirty is True
    assert window._on_close_request(window) is True
    # cancelling leaves the dirty flag in place
    assert window._dirty is True
    window.destroy()
    app.quit()


def test_channel_order_editor_cancel_and_confirm(broadway_display):
    controller = LayoutController()
    controller.load_demo(FIXTURES)
    app = MonitorLayoutApp(demo=True, controller=controller,
                           application_id="org.variablethe.MonitorLayout.x" + uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    mid = controller.layout.monitors[0].id
    controller.map_device(mid, controller.sinks[0].name, speaker_confirmed=True)
    def children(widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from children(child)
            child = child.get_next_sibling()
    def editor():
        return next(w for w in Gtk.Window.list_toplevels() if w.get_title() == "Confirm physical channel order")
    window.on_channel_order(mid)
    dialog = editor()
    next(w for w in children(dialog) if isinstance(w, Gtk.Button) and w.get_label() == "Cancel").emit("clicked")
    assert controller.mapping_for(mid).channel_order == []
    window.on_channel_order(mid)
    dialog = editor()
    choices = [w for w in children(dialog) if isinstance(w, Gtk.DropDown)]
    choices[0].set_selected(2)
    choices[1].set_selected(1)
    next(w for w in children(dialog) if isinstance(w, Gtk.Button) and w.get_label() == "Confirm order").emit("clicked")
    assert controller.mapping_for(mid).channel_order == list(reversed(controller.sinks[0].channels[:2]))
    assert controller.mapping_for(mid).confirmed_speakers == 0
    window.destroy()
    app.quit()
