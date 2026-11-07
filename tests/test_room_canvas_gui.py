"""Canvas-only checks run under the task-owned Broadway test runner."""
import os
import pytest
if os.environ.get("PW_GUI_TESTS") != "1":
    pytest.skip("Use scripts/run_gui_tests.py", allow_module_level=True)
pytest.importorskip("gi")
import cairo
import gi
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk
from monitor_layout.canvas import LayoutCanvas, RoomScene
from monitor_layout.controller import LayoutController


def canvas_with_monitor():
    controller = LayoutController()
    mon = controller.add_monitor(x=0, y=0)
    controller.set_room_geometry(mon.id, x=0, y=-80, yaw=20)
    controller.set_listener(x=0, y=0, facing=0)
    canvas = LayoutCanvas(controller)
    canvas.get_width = lambda: 720
    canvas.get_height = lambda: 460
    return controller, canvas, mon


def test_room_scene_render_hit_and_rotation(broadway_display):
    ctl, canvas, mon = canvas_with_monitor()
    canvas.set_view_mode("room")
    scene = canvas.scene()
    assert isinstance(scene, RoomScene)
    scene.compute_transform(720, 460)
    assert scene.hit_test(*scene.to_px(mon.room_x, mon.room_y)) == mon.id
    assert scene.hit_test(*scene.to_px(0, 0)) == "__listener__"
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 720, 460)
    scene.render(cairo.Context(surface), 720, 460)
    canvas.select(mon.id)
    assert canvas.key_pressed(Gdk.KEY_r, 0)
    assert mon.room_yaw == 35
    assert mon.orientation == "landscape"
    canvas.select("__listener__")
    assert canvas.key_pressed(Gdk.KEY_r, 0)
    assert ctl.layout.listener_facing == 15
    assert not canvas.key_pressed(Gdk.KEY_Delete, 0)
    canvas.set_view_mode("front")
    assert canvas.selected_id is None


@pytest.mark.parametrize("room,seat", [(False, False), (True, False), (True, True)])
def test_drag_exact_distance_and_reverse(broadway_display, room, seat):
    ctl, canvas, mon = canvas_with_monitor()
    canvas.set_view_mode("room" if room else "front")
    scene = canvas.scene()
    scene.compute_transform(720, 460)
    if seat:
        point = (ctl.layout.listener_x, ctl.layout.listener_y)
    elif room:
        point = (mon.room_x, mon.room_y)
    else:
        point = (mon.rect().center_x, mon.rect().center_y)
    original = (mon.x, mon.y, mon.room_x, mon.room_y)
    canvas._on_drag_begin(None, *scene.to_px(*point))
    assert canvas._drag_id == ("__listener__" if seat else mon.id)
    scale, origin = canvas._drag_scale, canvas._drag_origin_px
    def position():
        if seat:
            return ctl.layout.listener_x, ctl.layout.listener_y
        return (mon.room_x, mon.room_y) if room else (mon.x, mon.y)
    start = position()
    canvas._on_drag_update(None, 5 * scale, 0)
    assert position() == pytest.approx((start[0]+5, start[1]))
    # A draw between updates must not refit and change the drag transform.
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 720, 460)
    canvas._on_draw(canvas, cairo.Context(surface), 720, 460)
    assert (canvas._drag_scale, canvas._drag_origin_px) == (scale, origin)
    canvas._on_drag_update(None, -5 * scale, -3 * scale)
    assert position() == pytest.approx((start[0]-5, start[1]-3))
    canvas._on_drag_end(None, 0, 0)
    if room:
        assert (mon.x, mon.y) == original[:2]
    else:
        assert (mon.room_x, mon.room_y) == original[2:]


def test_peripheral_room_selection_and_safe_remove(broadway_display):
    from monitor_layout.model import Peripheral
    ctl, canvas, mon = canvas_with_monitor()
    peripheral = Peripheral("speaker-1", "Desk speaker", room_x=80, room_y=15,
                            room_yaw=30, speaker_confirmed=True)
    ctl.layout.peripherals.append(peripheral)
    canvas.set_view_mode("room")
    scene = canvas.scene()
    scene.compute_transform(720, 460)
    px, py = scene.to_px(80, 15)
    canvas._on_click(None, 1, px, py)
    assert canvas.selected_id == peripheral.id
    assert canvas.key_pressed(Gdk.KEY_r, 0)
    assert peripheral.room_yaw == 45
    assert canvas.key_pressed(Gdk.KEY_Right, 0)
    assert peripheral.room_x == 81
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 720, 460)
    canvas.scene().render(cairo.Context(surface), 720, 460)
    assert canvas.key_pressed(Gdk.KEY_Delete, 0)
    assert ctl.layout.peripherals == []
    assert canvas.selected_id is None


@pytest.fixture
def room_window(broadway_display):
    import uuid
    from pathlib import Path
    from monitor_layout.gui import MonitorLayoutApp
    ctl = LayoutController()
    ctl.load_demo(Path(__file__).resolve().parent.parent / "fixtures")
    app = MonitorLayoutApp(demo=True, controller=ctl,
                          application_id="org.variablethe.MonitorLayout.x"+uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    yield window
    window.destroy()
    app.quit()


def test_window_room_toggle_seat_and_speaker_inspector(room_window):
    from monitor_layout.audiodev import AudioSink
    window = room_window
    ctl = window.controller
    sink = AudioSink(900, "test_usb_speaker", "USB speaker", ["FL", "FR"], kind="speaker", transport="usb")
    ctl.sinks.append(sink)
    window.view_drop.set_selected(1)
    assert window.canvas.view_mode == "room"
    window._select_room("__listener__")
    window.room_x.set_value(12)
    window.room_y.set_value(95)
    window.room_yaw.set_value(25)
    assert (ctl.layout.listener_x, ctl.layout.listener_y, ctl.layout.listener_facing) == (12, 95, 25)
    assert ctl.layout.listener_confirmed
    window.on_add_speaker(sink)
    speaker = ctl.layout.peripherals[0]
    assert window.canvas.selected_id == speaker.id
    window.room_x.set_value(-45)
    window.room_yaw.set_value(40)
    window.spin_speakers.set_value(2)
    assert speaker.room_x == -45
    assert speaker.room_yaw == 40
    assert speaker.speaker_count == 2
    window.refresh(force=True)  # peripheral lacks monitor-only orientation/edge fields
    window.view_drop.set_selected(0)
    assert window.canvas.view_mode == "front"
    assert window.canvas.selected_id is None


def test_unknown_speaker_confirmation_cancel_accept(room_window):
    from gi.repository import Adw, Gtk
    from monitor_layout.audiodev import AudioSink
    window = room_window
    sink = AudioSink(901, "test_bluetooth_unknown", "Bluetooth output", ["MONO"], transport="bluetooth")
    window.controller.sinks.append(sink)
    def dialog():
        return next(w for w in Gtk.Window.list_toplevels()
                    if isinstance(w, Adw.MessageDialog) and w.get_heading() == "Is this output a physical speaker?")
    window.on_add_speaker(sink)
    dialog().emit("response", "cancel")
    assert window.controller.layout.peripherals == []
    window.on_add_speaker(sink)
    dialog().emit("response", "speaker")
    assert len(window.controller.layout.peripherals) == 1
    assert window.controller.layout.peripherals[0].speaker_confirmed
    window.on_add_speaker(sink)  # repeated confirmation cannot duplicate mapped sink
    dialog().emit("response", "speaker")
    assert len(window.controller.layout.peripherals) == 1


def test_demo_repeated_discovery_never_calls_live(room_window, monkeypatch):
    from monitor_layout import audiodev, detect
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo must not call live discovery")
    monkeypatch.setattr(audiodev, "discover_sinks", forbidden)
    monkeypatch.setattr(detect, "detect_monitors", forbidden)
    window = room_window
    ids = [m.id for m in window.controller.layout.monitors]
    for _ in range(2):
        window.on_detect_displays()
        window.on_detect_outputs()
    assert [m.id for m in window.controller.layout.monitors] == ids



def test_reset_keeps_listener_default_and_no_required_main(room_window):
    window = room_window
    ctl = window.controller
    ctl.set_main(ctl.layout.monitors[0].id)
    ctl.set_reference_mode("main")
    ctl.set_listener(x=45, y=180, facing=90)
    window.action_reset()
    assert ctl.layout.reference_mode == "listener"
    assert ctl.layout.main() is None
    assert ctl.layout.targets() == []
    assert (ctl.layout.listener_x, ctl.layout.listener_y, ctl.layout.listener_facing) == (0, 70, 0)
    assert not ctl.layout.listener_confirmed
    added = ctl.add_monitor(x=20)
    assert ctl.layout.main() is None
    assert ctl.preview().ok
    assert ctl.layout.listener_x == added.rect().center_x


def test_room_inspector_contextual_and_before_secondary_groups(room_window):
    from gi.repository import Gtk
    window = room_window
    assert not window.group_room.get_visible()
    window.view_drop.set_selected(1)
    assert window.group_room.get_visible()
    def descendants(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from descendants(child)
            child = child.get_next_sibling()
    groups = [widget for widget in descendants(window.sidebar_page)
              if widget in (window.group_displays, window.group_room, window.group_selected,
                            window.group_outputs, window.group_preview)]
    assert groups == [window.group_displays, window.group_room, window.group_selected,
                      window.group_outputs, window.group_preview]
    buttons = {widget.get_label(): widget for widget in descendants(window.group_room)
               if isinstance(widget, Gtk.Button)}
    buttons["Select seat"].emit("clicked")
    assert window.canvas.selected_id == "__listener__"
    ctl = window.controller
    ctl.set_main(ctl.layout.monitors[0].id)
    buttons["Use seated position / no main required"].emit("clicked")
    assert ctl.layout.main() is None
    assert ctl.layout.reference_mode == "listener"
    window.view_drop.set_selected(0)
    assert not window.group_room.get_visible()


@pytest.mark.parametrize("view", ["front", "room"])
def test_initial_view_selected_before_present(broadway_display, monkeypatch, view):
    import uuid
    from pathlib import Path
    from monitor_layout.gui import MonitorLayoutApp, MonitorLayoutWindow
    ctl = LayoutController()
    ctl.load_demo(Path(__file__).resolve().parent.parent / "fixtures")
    observed = []
    original = MonitorLayoutWindow.present
    def record_present(window):
        observed.append((window.canvas.view_mode, window.group_room.get_visible()))
        original(window)
    monkeypatch.setattr(MonitorLayoutWindow, "present", record_present)
    app = MonitorLayoutApp(demo=True, controller=ctl, initial_view=view,
                          application_id="org.variablethe.MonitorLayout.x"+uuid.uuid4().hex)
    app.register(None)
    app.activate()
    window = app.get_active_window()
    assert observed == [(view, view == "room")]
    assert window.canvas.view_mode == view
    assert ctl.demo_fixtures is not None
    window.destroy()
    app.quit()


def test_initial_view_rejects_unknown_value(broadway_display):
    from monitor_layout.gui import MonitorLayoutApp
    with pytest.raises(ValueError, match="initial_view"):
        MonitorLayoutApp(demo=True, initial_view="sideways")



def _descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _descendants(child)
        child = child.get_next_sibling()


def test_unmap_disconnected_output_keeps_target_geometry(room_window):
    from gi.repository import Gtk
    from monitor_layout.audiodev import AudioSink
    window = room_window
    ctl = window.controller
    ctl.sinks = [AudioSink(900, "test_speaker", "Speaker", ["MONO"], kind="speaker")]
    target = ctl.add_peripheral("test_speaker")
    ctl.set_room_geometry(target.id, x=-45, y=20, yaw=30)
    before = (target.room_x, target.room_y, target.room_yaw)
    ctl.sinks = []
    ctl.notify(False)
    unmap = next(widget for widget in _descendants(window.group_outputs)
                 if isinstance(widget, Gtk.Button) and widget.get_label() == "Unmap")
    unmap.emit("clicked")
    assert ctl.mapping_for(target.id) is None
    assert ctl.layout.get(target.id) is target
    assert (target.room_x, target.room_y, target.room_yaw) == before
    assert not any(isinstance(widget, Gtk.Button) and widget.get_label() == "Unmap"
                   for widget in _descendants(window.group_outputs))


def test_same_name_rediscovery_rebuilds_metadata_and_candidates(room_window):
    from gi.repository import Gtk, Adw
    from monitor_layout.audiodev import AudioSink
    window = room_window
    ctl = window.controller
    name = "same_named_output"
    ctl.sinks = [AudioSink(900, name, "Old USB speaker", ["FL", "FR"], kind="speaker", transport="usb")]
    mid = ctl.layout.monitors[0].id
    ctl.map_device(mid, name)
    old_rows = list(window._output_rows)
    replacement = AudioSink(901, name, "New headphone profile", ["MONO"],
                           kind="headphones", transport="bluetooth", form_factor="headphone")
    ctl.sinks = [replacement]
    ctl.notify(False)
    assert window._output_rows[0] is not old_rows[0]
    info = next(row for row in window._output_rows if row.get_title() == "Reported channels")
    assert info.get_subtitle() == "MONO (New headphone profile)"
    assert not any(row.get_title() == "Old USB speaker" for row in window._output_rows)
    dropdowns = [widget for widget in _descendants(window.group_outputs)
                 if isinstance(widget, Gtk.DropDown)]
    assert dropdowns
    assert all(drop.get_model().get_n_items() == 1 for drop in dropdowns)
    # Same node/name again, now unknown: UI must bind the newly discovered
    # object, not the old speaker's confirmation-bypassing callback.
    ctl.unmap_device(mid)
    unknown = AudioSink(901, name, "Unknown Bluetooth output", ["AUX0"], transport="bluetooth")
    ctl.sinks = [unknown]
    ctl.notify(False)
    row = next(row for row in window._output_rows if row.get_title() == unknown.description)
    button = next(widget for widget in _descendants(row)
                  if isinstance(widget, Gtk.Button) and widget.get_label() == "Confirm speaker…")
    button.emit("clicked")
    dialog = next(w for w in Gtk.Window.list_toplevels()
                  if isinstance(w, Adw.MessageDialog) and w.get_heading() == "Is this output a physical speaker?")
    assert unknown.description in dialog.get_body()
    dialog.emit("response", "cancel")
    assert ctl.layout.peripherals == []
