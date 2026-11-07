"""GTK4 / Libadwaita user interface for the monitor-speaker layout.

Safety properties of this module:

* It never calls ``wpctl set-*`` or any other write operation.  Discovery is
  read-only (``pw-dump``), and even that is skipped in demo mode.
* Saving a profile, exporting a fragment or installing it is always an
  explicit user action; nothing happens implicitly when the window closes.
* The legacy ``audio-output-controller.py`` is untouched - it is a separate
  tool and is not started by this application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import apply as apply_mod
from .controller import LayoutController
from .canvas import LayoutCanvas
from .model import ORIENTATIONS, SPEAKER_EDGES, LayoutError, Peripheral
from .panning import INPUT_CHANNELS

APP_ID = "org.variablethe.MonitorLayout"

ORIENTATION_LABELS = ("Landscape", "Portrait")
EDGE_LABELS = ("Bottom", "Top", "Left", "Right")

CSS = b"""
.layout-canvas {
    background-color: @window_bg_color;
}

.sidebar {
    background-color: @sidebar_bg_color;
}

.channel-label {
    font-family: monospace;
    font-weight: bold;
}

.gain-text {
    font-family: monospace;
    font-size: 0.9em;
}
"""




def _group_children(widget):
    """GTK4 has no get_children(); walk first_child/next_sibling."""
    out = []
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


class MonitorLayoutWindow(Adw.ApplicationWindow):
    """The single window of the application."""

    def __init__(self, app: Adw.Application, controller: LayoutController) -> None:
        super().__init__(application=app)
        self.controller = controller
        self.set_title("Monitor Layout")
        self.set_default_size(1080, 720)
        self.set_size_request(720, 520)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.banner = Adw.Banner()
        self.banner.set_revealed(False)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.add_top_bar(self.banner)
        toolbar.set_content(self._build_body())
        self.toasts.set_child(toolbar)

        self.canvas = LayoutCanvas(self.controller)
        # Attach the real canvas widget to the visible pane; otherwise the
        # central surface would be only its header hint.
        self.canvas_box.append(self.canvas)

        self.controller.connect(self._on_model_changed)

        self._register_actions()

        self._signature = None
        self._dirty = False
        self._force_close = False
        self.connect("close-request", self._on_close_request)

        # Initialise selection to the main/first display so the refinement
        # panel is never blank for a populated layout.
        if self.controller.layout.monitors:
            main = self.controller.layout.main()
            selected = main.id if main is not None else self.controller.layout.monitors[0].id
            self.canvas._selected_id = selected

        self._updating = False
        self.refresh(force=True)

    # ------------------------------------------------------------------ build

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        detect_btn = Gtk.Button.new_from_icon_name("video-display-symbolic")
        detect_btn.set_tooltip_text("Detect connected displays")
        detect_btn.connect("clicked", lambda _b: self.on_detect_displays())
        header.pack_start(detect_btn)

        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_btn.set_tooltip_text("Add a display")
        add_btn.connect("clicked", lambda _b: self.on_add_monitor())
        header.pack_start(add_btn)

        save_btn = Gtk.Button.new_from_icon_name("document-save-symbolic")
        save_btn.set_tooltip_text("Save layout")
        save_btn.connect("clicked", lambda _b: self.on_save_clicked())
        header.pack_end(save_btn)

        load_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        load_btn.set_tooltip_text("Load layout")
        load_btn.connect("clicked", lambda _b: self.on_load_clicked())
        header.pack_end(load_btn)

        export_btn = Gtk.Button.new_from_icon_name("document-export-symbolic")
        export_btn.set_tooltip_text("Export PipeWire configuration")
        export_btn.connect("clicked", lambda _b: self.on_export_clicked())
        header.pack_end(export_btn)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Install into PipeWire…", "win.install")
        section.append("Reset layout", "win.reset")
        section.append("About", "win.about")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton.new()
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text("More options")
        header.pack_end(menu_button)
        return header

    def _build_body(self) -> Gtk.Widget:
        paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        paned.set_position(380)
        paned.set_vexpand(True)

        self.sidebar = Gtk.ScrolledWindow()
        self.sidebar.add_css_class("sidebar")
        self.sidebar.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sidebar.set_size_request(320, -1)
        self.sidebar_page = Adw.PreferencesPage()
        self.sidebar.set_child(self.sidebar_page)
        paned.set_start_child(self.sidebar)

        self.canvas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.hint = Gtk.Label(label="Drag a display to move it. Arrows nudge, R rotates, M sets main.")
        self.hint.add_css_class("dim-label")
        self.hint.set_margin_top(6)
        self.hint.set_margin_bottom(8)
        self.canvas_box.append(self.hint)
        self.view_drop = Gtk.DropDown.new_from_strings(["Displays · front view", "Listening area · top down"])
        self.view_drop.connect("notify::selected", self._on_view_changed)
        self.canvas_box.append(self.view_drop)
        paned.set_end_child(self.canvas_box)

        self._build_groups()
        return paned

    def _build_groups(self) -> None:
        page = self.sidebar_page

        self.group_displays = Adw.PreferencesGroup(
            title="Displays", description="Detected displays in your desktop arrangement"
        )
        page.add(self.group_displays)

        self.group_selected = Adw.PreferencesGroup(
            title="Selected display", description="Refine the highlighted display"
        )
        page.add(self.group_selected)

        self.group_outputs = Adw.PreferencesGroup(
            title="Audio outputs",
            description="Map each display to a PipeWire output and confirm its speakers",
        )
        page.add(self.group_outputs)

        self.group_preview = Adw.PreferencesGroup(
            title="Route preview",
            description="How much of each input channel reaches each speaker",
        )
        page.add(self.group_preview)

        note = Adw.PreferencesGroup()
        note.set_title("Notes")
        note.add(
            Adw.ActionRow(
                title="Stereo widening, not discrete surround",
                subtitle=(
                    "Extra speakers get a blend of the stereo channels. Desktop "
                    "positions are approximate until you place them in the listening area."
                ),
            )
        )
        note.add(
            Adw.ActionRow(
                title="Nothing is applied automatically",
                subtitle=(
                    "Saving, exporting and installing are explicit actions; your "
                    "live audio is never changed behind your back."
                ),
            )
        )
        page.add(note)

        self.group_room = Adw.PreferencesGroup(title="Listening area", description="Centimeters, +X right, +Y down. Facing 0° looks up. Initial positions are approximate, not measured.")
        page.add(self.group_room)
        reference = Gtk.Button(label="Use seated position / no main required")
        reference.connect("clicked", lambda *_: self.controller.clear_main())
        self.group_room.add(reference)
        seat = Gtk.Button(label="Select seat")
        seat.connect("clicked", lambda *_: self._select_room("__listener__"))
        self.group_room.add(seat)
        room_grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.room_x = self._precision_spin("Room X", room_grid, 0)
        self.room_y = self._precision_spin("Room Y", room_grid, 1)
        self.room_yaw = self._precision_spin("Facing / yaw °", room_grid, 2)
        self.room_width = self._precision_spin("Speaker span", room_grid, 3)
        for spin in (self.room_x, self.room_y, self.room_yaw, self.room_width):
            spin.connect("value-changed", self._on_room_changed)
        self.group_room.add(room_grid)
        self.room_status = Gtk.Label(wrap=True, xalign=0)
        self.group_room.add(self.room_status)
        confirm_room = Gtk.Button(label="Confirm this room position")
        confirm_room.connect("clicked", lambda *_: self._on_room_changed(None))
        self.group_room.add(confirm_room)
        # PreferencesPage exposes add/remove, not a public reorder operation.
        # Re-add the secondary groups after the contextual listening inspector.
        for group in (self.group_selected, self.group_outputs, self.group_preview, note):
            page.remove(group)
            page.add(group)
        self.group_room.set_visible(False)
        self._build_selected_rows()
        self._display_rows = []
        self._output_rows = []
        self._preview_rows = []

    def _build_selected_rows(self) -> None:
        group = self.group_selected

        self.entry_name = Adw.EntryRow(title="Name")
        self.entry_name.connect("changed", self._on_name_changed)
        group.add(self.entry_name)

        self.drop_orientation = Gtk.DropDown.new_from_strings(list(ORIENTATION_LABELS))
        self.drop_orientation.set_valign(Gtk.Align.CENTER)
        self.drop_orientation.connect("notify::selected", self._on_orientation_changed)
        row = Adw.ActionRow(title="Orientation", subtitle="Rotate the display")
        row.add_suffix(self.drop_orientation)
        group.add(row)

        self.btn_rotate = Gtk.Button(label="Rotate")
        self.btn_rotate.set_valign(Gtk.Align.CENTER)
        self.btn_rotate.add_css_class("flat")
        self.btn_rotate.connect("clicked", lambda _b: self.on_rotate())
        row = Adw.ActionRow(title="Quick rotate")
        row.add_suffix(self.btn_rotate)
        group.add(row)

        self.spin_speakers = Gtk.SpinButton.new(
            Gtk.Adjustment(value=2, lower=1, upper=4, step_increment=1, page_increment=1), 1, 0
        )
        self.spin_speakers.set_valign(Gtk.Align.CENTER)
        self.spin_speakers.connect("value-changed", self._on_speaker_count_changed)
        row = Adw.ActionRow(title="Speakers", subtitle="Confirmed physical drivers per display")
        row.add_suffix(self.spin_speakers)
        group.add(row)

        self.drop_edge = Gtk.DropDown.new_from_strings(list(EDGE_LABELS))
        self.drop_edge.set_valign(Gtk.Align.CENTER)
        self.drop_edge.connect("notify::selected", self._on_edge_changed)
        row = Adw.ActionRow(title="Speaker edge (viewer-relative)", subtitle="Orientation does not auto-calibrate speakers")
        row.add_suffix(self.drop_edge)
        group.add(row)

        self.btn_main = Gtk.Button(label="Set as main")
        self.btn_main.set_valign(Gtk.Align.CENTER)
        self.btn_main.add_css_class("suggested-action")
        self.btn_main.connect("clicked", lambda _b: self.on_set_main())
        row = Adw.ActionRow(title="Main display", subtitle="The one you mainly look at")
        row.add_suffix(self.btn_main)
        group.add(row)

        # precision tools (optional refinement of the direct manipulation)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        self.spin_x = self._precision_spin("X", grid, 0)
        self.spin_y = self._precision_spin("Y", grid, 1)
        self.spin_w = self._precision_spin("Width", grid, 2)
        self.spin_h = self._precision_spin("Height", grid, 3)
        for spin, (label, _r, _c) in (
            (self.spin_x, ("X", 0, 0)),
            (self.spin_y, ("Y", 1, 0)),
            (self.spin_w, ("Width", 2, 1)),
            (self.spin_h, ("Height", 3, 1)),
        ):
            spin.connect("value-changed", self._on_precision_changed)
        row = Adw.ActionRow(title="Exact position and size")
        row.add_suffix(grid)
        group.add(row)

    def _precision_spin(self, label: str, grid: Gtk.Grid, row: int) -> Gtk.SpinButton:
        lbl = Gtk.Label(label=label)
        lbl.set_valign(Gtk.Align.CENTER)
        grid.attach(lbl, 0, row, 1, 1)
        spin = Gtk.SpinButton.new(
            Gtk.Adjustment(value=0, lower=-500, upper=500, step_increment=0.5, page_increment=5),
            1,
            1,
        )
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_width_chars(6)
        grid.attach(spin, 1, row, 1, 1)
        return spin



    def _track(self, group, row, bucket):
        group.add(row)
        bucket.append(row)

    # --------------------------------------------------------------- refreshing

    def _on_model_changed(self, layout_changed: bool = True) -> None:
        if layout_changed:
            self._dirty = True
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        signature = self._current_signature()
        if force or signature != self._signature:
            self._signature = signature
            self._rebuild_displays()
            self._rebuild_outputs()
        self._refresh_selected()
        self._refresh_room()
        self._rebuild_preview()
        self.canvas.refresh()

    def _current_signature(self) -> tuple:
        items = []
        for m in self.controller.layout.targets():
            mapping = self.controller.mapping_for(m.id)
            items.append(
                (
                    m.id,
                    m.name,
                    getattr(m, "orientation", "landscape"),
                    m.speaker_count,
                    getattr(m, "speaker_edge", "bottom"),
                    getattr(m, "is_main", False),
                    mapping.device if mapping else None,
                    mapping.confirmed_speakers if mapping else None,
                    tuple(mapping.channel_order) if mapping else (),
                    mapping.speaker_confirmed if mapping else False,
                )
            )
        sinks = tuple(
            (s.node_id, s.name, s.description, tuple(s.channels), s.is_virtual,
             tuple(s.ports), s.kind, s.transport, s.form_factor)
            for s in self.controller.sinks
        )
        return (tuple(items), sinks, self.controller.discovery_error)

    # -- displays list -------------------------------------------------------

    def _rebuild_displays(self) -> None:
        group = self.group_displays
        for row in self._display_rows:
            group.remove(row)
        self._display_rows = []
        monitors = self.controller.layout.monitors
        if not monitors:
            row = Adw.ActionRow(
                title="No displays yet",
                subtitle="Use “Detect displays” or the + button.",
            )
            self._track(group, row, self._display_rows)
            return
        numbers = self.controller.monitor_numbers()
        for index, mon in enumerate(monitors):
            mapping = self.controller.mapping_for(mon.id)
            subtitle_bits = [f"{mon.speaker_count} speaker(s)"]
            if mon.orientation == "portrait":
                subtitle_bits.append("portrait")
            if mon.is_main:
                subtitle_bits.append("main")
            if mapping and mapping.device:
                state = (
                    "confirmed"
                    if mapping.confirmed_speakers == mon.speaker_count
                    else "not confirmed"
                )
                subtitle_bits.append(f"{mapping.device} ({state})")
            else:
                subtitle_bits.append("no output mapped")
            row = Adw.ActionRow(
                title=f"{numbers[mon.id]}. {mon.name}",
                subtitle=" · ".join(subtitle_bits),
            )
            row.set_activatable(True)
            row.connect("activated", lambda _r, mid=mon.id: self.canvas.select(mid))
            if mon.is_main:
                row.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
            else:
                btn = Gtk.Button.new_from_icon_name("star-new-symbolic")
                btn.add_css_class("flat")
                btn.set_tooltip_text("Set as main display")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda _b, mid=mon.id: self.on_set_main(mid))
                row.add_suffix(btn)
            rot = Gtk.Button.new_from_icon_name("object-rotate-left-symbolic")
            rot.add_css_class("flat")
            rot.set_tooltip_text("Rotate")
            rot.set_valign(Gtk.Align.CENTER)
            rot.connect("clicked", lambda _b, mid=mon.id: self.on_rotate(mid))
            row.add_suffix(rot)
            up = Gtk.Button.new_from_icon_name("go-up-symbolic")
            up.add_css_class("flat")
            up.set_tooltip_text("Move up in the list")
            up.set_valign(Gtk.Align.CENTER)
            up.set_sensitive(index > 0)
            up.connect("clicked", lambda _b, mid=mon.id: self.on_reorder(mid, -1))
            row.add_suffix(up)
            down = Gtk.Button.new_from_icon_name("go-down-symbolic")
            down.add_css_class("flat")
            down.set_tooltip_text("Move down in the list")
            down.set_valign(Gtk.Align.CENTER)
            down.set_sensitive(index < len(monitors) - 1)
            down.connect("clicked", lambda _b, mid=mon.id: self.on_reorder(mid, +1))
            row.add_suffix(down)
            rm = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            rm.add_css_class("flat")
            rm.set_tooltip_text("Remove display")
            rm.set_valign(Gtk.Align.CENTER)
            rm.connect("clicked", lambda _b, mid=mon.id: self.on_remove(mid))
            row.add_suffix(rm)
            self._track(group, row, self._display_rows)

    def _select_room(self, target_id):
        self.view_drop.set_selected(1)
        self.canvas.set_view_mode("room")
        self.canvas.select(target_id)

    def _on_view_changed(self, dropdown, *_):
        if not hasattr(self, "canvas"):
            return
        room = dropdown.get_selected() == 1
        self.group_room.set_visible(room)
        self.canvas.set_view_mode("room" if room else "front")
        self.hint.set_text("Drag speakers or your seat. R turns by 15°. Arrows nudge." if room else "Front elevation: drag displays, R rotates. Height is not room depth.")
        self.refresh()

    def _refresh_room(self):
        layout = self.controller.layout
        target = layout.get(self.canvas.selected_id)
        seat = self.canvas.selected_id == "__listener__"
        self._updating = True
        try:
            for widget in (self.room_x, self.room_y, self.room_yaw):
                widget.set_sensitive(seat or target is not None)
            self.room_width.set_sensitive(isinstance(target, Peripheral))
            self.room_width.set_value(target.width if isinstance(target, Peripheral) else 20)
            if seat:
                values = (layout.listener_x, layout.listener_y, layout.listener_facing)
                confirmed = layout.listener_confirmed
            elif target:
                values = (target.room_x, target.room_y, target.room_yaw)
                confirmed = target.room_confirmed
            else:
                values, confirmed = (0, 0, 0), False
            for widget, value in zip((self.room_x, self.room_y, self.room_yaw), values):
                widget.set_value(value)
            self.room_status.set_text(("Manual / confirmed room position" if confirmed else "Approximate initial position: place or confirm in top-down view") + (" · legacy main reference active" if layout.reference_mode == "main" else " · seated reference active"))
        finally:
            self._updating = False

    def _on_room_changed(self, *_):
        if self._updating:
            return
        values = dict(x=self.room_x.get_value(), y=self.room_y.get_value())
        try:
            if self.canvas.selected_id == "__listener__":
                self.controller.set_listener(**values, facing=self.room_yaw.get_value())
            elif self.canvas.selected_id:
                self.controller.set_room_geometry(self.canvas.selected_id, **values, yaw=self.room_yaw.get_value(), width=self.room_width.get_value() if isinstance(self.controller.layout.get(self.canvas.selected_id), Peripheral) else None)
        except LayoutError as exc:
            self.toast(str(exc))

    # -- selected display ----------------------------------------------------

    def _refresh_selected(self) -> None:
        mon_id = self.canvas.selected_id
        mon = self.controller.layout.get(mon_id) if mon_id else None
        self._updating = True
        try:
            if mon is None:
                self.entry_name.set_text("")
                self.entry_name.set_sensitive(False)
                for w in (
                    self.drop_orientation,
                    self.btn_rotate,
                    self.spin_speakers,
                    self.drop_edge,
                    self.btn_main,
                    self.spin_x,
                    self.spin_y,
                    self.spin_w,
                    self.spin_h,
                ):
                    w.set_sensitive(False)
                return
            if isinstance(mon, Peripheral):
                self.entry_name.set_sensitive(True)
                self.entry_name.set_text(mon.name)
                self.spin_speakers.set_sensitive(True)
                self.spin_speakers.set_value(mon.speaker_count)
                for w in (self.drop_orientation, self.btn_rotate, self.drop_edge, self.btn_main,
                          self.spin_x, self.spin_y, self.spin_w, self.spin_h):
                    w.set_sensitive(False)
                return
            for w in (
                self.entry_name,
                self.drop_orientation,
                self.btn_rotate,
                self.spin_speakers,
                self.drop_edge,
                self.btn_main,
                self.spin_x,
                self.spin_y,
                self.spin_w,
                self.spin_h,
            ):
                w.set_sensitive(True)
            self.entry_name.set_text(mon.name)
            self.drop_orientation.set_selected(
                ORIENTATIONS.index(mon.orientation) if mon.orientation in ORIENTATIONS else 0
            )
            self.spin_speakers.set_value(mon.speaker_count)
            self.drop_edge.set_selected(
                SPEAKER_EDGES.index(mon.speaker_edge) if mon.speaker_edge in SPEAKER_EDGES else 0
            )
            self.spin_x.set_value(mon.x)
            self.spin_y.set_value(mon.y)
            self.spin_w.set_value(mon.width)
            self.spin_h.set_value(mon.height)
            self.btn_main.set_label("Main display" if mon.is_main else "Set as main")
            self.btn_main.set_sensitive(not mon.is_main)
        finally:
            self._updating = False

    # -- outputs -------------------------------------------------------------

    def _rebuild_outputs(self) -> None:
        group = self.group_outputs
        for row in self._output_rows:
            group.remove(row)
        self._output_rows = []

        detect = Gtk.Button(label="Detect outputs")
        detect.add_css_class("flat")
        detect.set_valign(Gtk.Align.CENTER)
        detect.connect("clicked", lambda _b: self.on_detect_outputs())
        row = Adw.ActionRow(title="PipeWire outputs", subtitle="Read-only discovery")
        row.add_suffix(detect)
        self._track(group, row, self._output_rows)

        if self.controller.discovery_error:
            err = Adw.ActionRow(title="Discovery failed", subtitle=self.controller.discovery_error)
            self._track(group, err, self._output_rows)
        if not self.controller.sinks:
            self._track(
                group,
                Adw.ActionRow(
                    title="No outputs discovered",
                    subtitle="Use demo fixtures or connect an output device.",
                ),
                self._output_rows,
            )

        for sink in self.controller.sinks:
            if sink.is_virtual or sink.kind == "headphones":
                continue
            add = Gtk.Button(label="Add speaker" if sink.auto_speaker_candidate else "Confirm speaker…")
            add.set_valign(Gtk.Align.CENTER)
            add.set_sensitive(sink.name not in self.controller.layout.mapped_device_names())
            add.connect("clicked", lambda _b, output=sink: self.on_add_speaker(output))
            row = Adw.ActionRow(title=sink.description, subtitle=sink.label)
            row.add_suffix(add)
            self._track(group, row, self._output_rows)
        for index, target in enumerate(self.controller.layout.peripherals):
            row = Adw.ActionRow(title=f"S{index + 1}. {target.name}", subtitle="Standalone speaker · select to place in Listening area")
            row.set_activatable(True)
            row.connect("activated", lambda _r, tid=target.id: self._select_room(tid))
            remove = Gtk.Button(label="Remove")
            remove.connect("clicked", lambda _b, tid=target.id: self.on_remove(tid))
            row.add_suffix(remove)
            self._track(group, row, self._output_rows)

        for mon in self.controller.layout.targets():
            mapping = self.controller.mapping_for(mon.id)
            current = mapping.device if mapping else None
            candidates = [s for s in self.controller.sinks if not s.is_virtual and s.kind != "headphones"]
            labels = ["(not connected)"] + [s.name for s in candidates]
            selected = labels.index(current) if current in labels else 0
            drop = Gtk.DropDown.new_from_strings(labels)
            drop.set_valign(Gtk.Align.CENTER)
            drop.set_selected(selected)
            drop.connect(
                "notify::selected",
                lambda d, _p, mid=mon.id, choices=candidates: self._on_device_changed(mid, d, choices),
            )
            row = Adw.ActionRow(title=mon.name, subtitle=current or "no output mapped")
            row.add_suffix(drop)
            if current:
                unmap = Gtk.Button(label="Unmap")
                unmap.set_valign(Gtk.Align.CENTER)
                unmap.set_tooltip_text("Clear this output mapping; keep its placement")
                unmap.connect("clicked", lambda _b, mid=mon.id: self.controller.unmap_device(mid))
                row.add_suffix(unmap)
            self._track(group, row, self._output_rows)

            if current:
                sink = next((s for s in self.controller.sinks if s.name == current), None)
                if sink and sink.requires_speaker_confirmation and not mapping.speaker_confirmed:
                    verify = Gtk.Button(label="Confirm speaker type…")
                    verify.connect("clicked", lambda _b, output=sink, mid=mon.id: self._confirm_unknown(output, lambda: self.controller.confirm_output_speaker(mid)))
                    verify_row = Adw.ActionRow(title="Unknown output type", subtitle="Confirm this is a speaker, not headphones")
                    verify_row.add_suffix(verify)
                    self._track(group, verify_row, self._output_rows)
                channels = ", ".join(sink.channels) if sink and sink.channels else "unknown"
                info = Adw.ActionRow(
                    title="Reported channels",
                    subtitle=(
                        f"{channels} ({sink.description})" if sink else "device not detected"
                    ),
                )
                self._track(group, info, self._output_rows)
                confirm = Gtk.Button(
                    label=(
                        "Confirm"
                        if mapping and mapping.confirmed_speakers == mon.speaker_count
                        else "Confirm count"
                    )
                )
                confirm.add_css_class("suggested-action")
                confirm.set_valign(Gtk.Align.CENTER)
                confirm.set_sensitive(mapping is not None)
                confirm.connect(
                    "clicked",
                    lambda _b, mid=mon.id, m=mon: self.on_confirm_speakers(mid, m.speaker_count),
                )
                row = Adw.ActionRow(
                    title="Physical speakers",
                    subtitle=(
                        f"{mon.speaker_count} speaker(s) on this display"
                        + (
                            " - confirmed"
                            if mapping and mapping.confirmed_speakers == mon.speaker_count
                            else " - needs confirmation"
                        )
                    ),
                )
                row.add_suffix(confirm)
                self._track(group, row, self._output_rows)

                order_button = Gtk.Button(label="Set channel order")
                order_button.set_valign(Gtk.Align.CENTER)
                order_button.connect("clicked", lambda _b, mid=mon.id: self.on_channel_order(mid))
                order_row = Adw.ActionRow(
                    title="Physical channel order",
                    subtitle=(", ".join(mapping.channel_order) if mapping.channel_order else "Not confirmed")
                    + " · left to right / top to bottom",
                )
                order_row.add_suffix(order_button)
                self._track(group, order_row, self._output_rows)

    def _confirm_unknown(self, sink, action):
        dialog = Adw.MessageDialog.new(self, "Is this output a physical speaker?",
            f"{sink.description} has no reliable speaker classification. Confirm only if it is a speaker, not headphones. Channel count is not driver count.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("speaker", "It is a speaker")
        dialog.set_default_response("cancel")
        def respond(d, response):
            d.destroy()
            if response == "speaker":
                try:
                    action()
                except LayoutError as exc:
                    self.toast(str(exc))
            self.refresh(force=True)
        dialog.connect("response", respond)
        dialog.present()

    def on_add_speaker(self, sink):
        def add():
            target = self.controller.add_peripheral(sink.name, speaker_confirmed=True)
            self._select_room(target.id)
            self.toast("Speaker added. Set physical count, channel order and room placement.")
        if sink.requires_speaker_confirmation:
            self._confirm_unknown(sink, add)
        else:
            try:
                add()
            except LayoutError as exc:
                self.toast(str(exc))

    def on_channel_order(self, monitor_id: str) -> None:
        mon = self.controller.layout.get(monitor_id)
        mapping = self.controller.mapping_for(monitor_id)
        labels = self.controller.device_channels(mapping.device) if mapping else None
        if not mon or not mapping or not labels:
            self.toast("Discover this output's channel labels first")
            return
        device, count, edge = mapping.device, mon.speaker_count, getattr(mon, "speaker_edge", "bottom")
        dialog = Gtk.Window(title="Confirm physical channel order", transient_for=self, modal=True)
        dialog.set_default_size(440, 280)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_bottom, box.set_margin_start, box.set_margin_end):
            setter(20)
        direction = "left to right" if edge in ("bottom", "top") else "top to bottom"
        explanation = Gtk.Label(label=f"{mon.name}: {edge} edge, viewed from your seat.\nAssign channels {direction}. Rotation does not calibrate\nphysical wiring. Confirm only an order you have verified.", wrap=True, xalign=0)
        box.append(explanation)
        choices = []
        for slot in range(count):
            row = Gtk.Box(spacing=12)
            row.append(Gtk.Label(label=f"Speaker {slot + 1}", hexpand=True, xalign=0))
            choice = Gtk.DropDown.new_from_strings(["Choose channel"] + labels)
            if len(mapping.channel_order) == count and mapping.channel_order[slot] in labels:
                choice.set_selected(labels.index(mapping.channel_order[slot]) + 1)
            row.append(choice)
            choices.append(choice)
            box.append(row)
        error = Gtk.Label(wrap=True, xalign=0)
        box.append(error)
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        confirm = Gtk.Button(label="Confirm order")
        confirm.add_css_class("suggested-action")
        def save(*_):
            current = self.controller.mapping_for(monitor_id)
            current_mon = self.controller.layout.get(monitor_id)
            if not current or current.device != device or not current_mon or (current_mon.speaker_count, getattr(current_mon, "speaker_edge", "bottom")) != (count, edge):
                error.set_text("Layout changed. Close and reopen this editor.")
                return
            selected = [int(choice.get_selected()) for choice in choices]
            if any(index == 0 or index > len(labels) for index in selected):
                error.set_text("Choose a channel for every physical speaker.")
                return
            try:
                self.controller.confirm_channel_order(monitor_id, [labels[index - 1] for index in selected])
            except LayoutError as exc:
                error.set_text(str(exc))
                return
            dialog.close()
        confirm.connect("clicked", save)
        actions.append(cancel)
        actions.append(confirm)
        box.append(actions)
        dialog.set_child(box)
        dialog.present()

    # -- preview -------------------------------------------------------------

    def _rebuild_preview(self) -> None:
        group = self.group_preview
        for row in self._preview_rows:
            group.remove(row)
        self._preview_rows = []
        outcome = self.controller.preview()
        if not outcome.ok:
            row = Adw.ActionRow(
                title="Nothing to preview",
                subtitle=outcome.errors[0] if outcome.errors else "",
            )
            self._track(group, row, self._preview_rows)
            return
        result = outcome.contributions
        assert result is not None
        for channel in result.channels:
            parts = []
            for key in result.speaker_keys:
                gain = result.matrix[channel].get(key, 0.0)
                if gain > 0:
                    parts.append(f"{key} {round(gain * 100)}%")
            label = Gtk.Label(label=channel)
            label.add_css_class("channel-label")
            label.set_valign(Gtk.Align.CENTER)
            text = Gtk.Label(label=" · ".join(parts) if parts else "silent")
            text.add_css_class("gain-text")
            text.set_valign(Gtk.Align.CENTER)
            text.set_wrap(True)
            text.set_xalign(0)
            row = Adw.ActionRow()
            row.set_title("")  # keep the row compact
            row.add_prefix(label)
            row.add_suffix(text)
            self._track(group, row, self._preview_rows)
        for key in result.speaker_keys:
            total = result.speaker_total(key)
            row = Adw.ActionRow(
                title=key,
                subtitle=f"total {round(total * 100)}% of one full-scale signal",
            )
            self._track(group, row, self._preview_rows)
        for warning in outcome.warnings:
            self._track(group, Adw.ActionRow(title="Note", subtitle=warning), self._preview_rows)

    # --------------------------------------------------------------- actions


    def _register_actions(self) -> None:
        for action_name, callback in (
            ("install", self.action_install),
            ("reset", self.action_reset),
            ("about", self.action_about),
        ):
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    def _on_close_request(self, window) -> bool:
        if self._force_close or not self._dirty:
            return False
        dialog = Adw.MessageDialog.new(
            self,
            "Close without saving?",
            "You have unsaved changes. Close anyway?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("close", "Close")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(d, response):
            if response == "close":
                self._force_close = True
                self._dirty = False
                d.destroy()
                self.destroy()
            else:
                d.destroy()

        dialog.connect("response", on_response)
        dialog.present()
        return True

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(message))

    def on_add_monitor(self) -> None:
        try:
            mon = self.controller.add_monitor(name=f"Monitor {len(self.controller.layout.monitors) + 1}")
        except LayoutError as exc:
            self.toast(str(exc))
            return
        self.canvas.select(mon.id)
        self.toast(f"Added {mon.name}")

    def on_remove(self, monitor_id: Optional[str] = None) -> None:
        monitor_id = monitor_id or self.canvas.selected_id
        if monitor_id is None:
            return
        try:
            self.controller.remove_target(monitor_id)
        except LayoutError as exc:
            self.toast(str(exc))
            return
        if self.canvas.selected_id == monitor_id:
            self.canvas.select(None)
        self.toast("Display removed")

    def on_rotate(self, monitor_id: Optional[str] = None) -> None:
        monitor_id = monitor_id or self.canvas.selected_id
        if monitor_id is None:
            return
        try:
            orientation = self.controller.rotate_monitor(monitor_id)
        except LayoutError as exc:
            self.toast(str(exc))
            return
        self.toast(f"Rotated to {orientation}")

    def on_set_main(self, monitor_id: Optional[str] = None) -> None:
        monitor_id = monitor_id or self.canvas.selected_id
        if monitor_id is None:
            return
        try:
            self.controller.set_main(monitor_id)
        except LayoutError as exc:
            self.toast(str(exc))
            return
        self.toast("Main display updated; seated reference stays independent")

    def on_reorder(self, monitor_id: str, delta: int) -> None:
        try:
            index = self.controller.monitor_index(monitor_id)
            self.controller.reorder(monitor_id, index + delta)
        except LayoutError as exc:
            self.toast(str(exc))

    def on_detect_displays(self) -> None:
        outcome = self.controller.discover()
        if outcome.error:
            self.banner.set_title("Display detection failed")
            self.banner.set_description(outcome.error)
            self.banner.set_revealed(True)
            self.toast("Could not detect displays")
            return
        self.banner.set_revealed(False)
        text = f"Detected {outcome.added} display(s)"
        if outcome.approximate:
            text += " (approximate geometry)"
        self.toast(text)
        for warning in outcome.warnings:
            self.toast(warning)

    def on_detect_outputs(self) -> None:
        error = self.controller.discover_sinks()
        if error:
            self.banner.set_title("Output discovery failed")
            self.banner.set_description(error)
            self.banner.set_revealed(True)
            return
        self.banner.set_revealed(False)
        self.toast(f"Found {len(self.controller.sinks)} output(s)")

    def on_confirm_speakers(self, monitor_id: str, count: int) -> None:
        try:
            self.controller.confirm_speaker_count(monitor_id, count)
        except LayoutError as exc:
            self.toast(str(exc))
            return
        self.toast(f"Confirmed {count} speaker(s)")

    def _on_device_changed(self, monitor_id: str, dropdown: Gtk.DropDown, candidates=None) -> None:
        index = dropdown.get_selected()
        if index <= 0:
            self.controller.unmap_device(monitor_id)
            self.toast("Output unmapped")
            return
        sink = (candidates or self.controller.sinks)[index - 1]
        device = sink.name
        if sink.requires_speaker_confirmation:
            self._confirm_unknown(sink, lambda: self.controller.map_device(monitor_id, device, speaker_confirmed=True))
            return
        mon = self.controller.layout.get(monitor_id)
        try:
            self.controller.map_device(monitor_id, device)
        except LayoutError as exc:
            self.toast(str(exc))
            return
        self.toast(f"Mapped {device} - confirm the speaker count")

    # -- widget callbacks ----------------------------------------------------

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        if self._updating:
            return
        mon_id = self.canvas.selected_id
        if mon_id is None:
            return
        try:
            self.controller.rename_monitor(mon_id, entry.get_text())
        except LayoutError:
            return

    def _on_orientation_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._updating:
            return
        mon_id = self.canvas.selected_id
        if mon_id is None:
            return
        try:
            self.controller.set_orientation(mon_id, ORIENTATIONS[dropdown.get_selected()])
        except LayoutError as exc:
            self.toast(str(exc))

    def _on_edge_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._updating:
            return
        mon_id = self.canvas.selected_id
        if mon_id is None:
            return
        try:
            self.controller.set_speaker_edge(mon_id, SPEAKER_EDGES[dropdown.get_selected()])
        except LayoutError as exc:
            self.toast(str(exc))

    def _on_speaker_count_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating:
            return
        mon_id = self.canvas.selected_id
        if mon_id is None:
            return
        try:
            self.controller.set_speaker_count(mon_id, int(spin.get_value()))
        except LayoutError as exc:
            self.toast(str(exc))

    def _on_precision_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating:
            return
        mon_id = self.canvas.selected_id
        if mon_id is None:
            return
        try:
            self.controller.set_geometry(
                mon_id,
                x=self.spin_x.get_value(),
                y=self.spin_y.get_value(),
                width=self.spin_w.get_value(),
                height=self.spin_h.get_value(),
            )
        except LayoutError as exc:
            self.toast(str(exc))

    # -- files ---------------------------------------------------------------

    def save_to(self, path: Path | str) -> None:
        """Explicit save; writes a profile file only."""
        try:
            saved = self.controller.save(path)
        except (LayoutError, OSError) as exc:
            self.toast(f"Save failed: {exc}")
            return
        self._dirty = False
        self.toast(f"Saved {saved.name}")

    def load_from(self, path: Path | str) -> None:
        try:
            self.controller.load(path)
        except LayoutError as exc:
            self.toast(f"Load failed: {exc}")
            return
        self._dirty = False
        self.canvas.select(None)
        self.toast("Layout loaded")

    def export_to(self, path: Path | str) -> tuple[bool, list[str]]:
        """Explicit export of a PipeWire fragment; writes a file only."""
        text, warnings, errors = self.controller.export_conf()
        if text is None:
            for err in errors:
                self.toast(err)
            return False, errors
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self.toast(f"Export failed: {exc}")
            return False, [str(exc)]
        for warning in warnings:
            self.toast(warning)
        self.toast(f"Exported {Path(path).name} - review it before installing")
        return True, warnings

    def on_save_clicked(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Save layout",
            self,
            Gtk.FileChooserAction.SAVE,
            "Save",
            "Cancel",
        )
        dialog.set_current_name("monitor-layout.json")
        dialog.set_modal(True)

        def on_response(d: Gtk.FileChooserNative, response: int) -> None:
            if response == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                if file is not None:
                    self.save_to(file.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    def on_load_clicked(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Load layout", self, Gtk.FileChooserAction.OPEN, "Open", "Cancel"
        )
        dialog.set_modal(True)

        def on_response(d: Gtk.FileChooserNative, response: int) -> None:
            if response == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                if file is not None:
                    self.load_from(file.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    def on_export_clicked(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Export PipeWire configuration",
            self,
            Gtk.FileChooserAction.SAVE,
            "Export",
            "Cancel",
        )
        dialog.set_current_name("monitor-layout.conf")
        dialog.set_modal(True)

        def on_response(d: Gtk.FileChooserNative, response: int) -> None:
            if response == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                if file is not None:
                    self.export_to(file.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    # -- application actions -------------------------------------------------

    def action_install(self) -> None:
        """Guarded, explicit install.  Never triggered automatically."""
        text, warnings, errors = self.controller.export_conf()
        if text is None:
            for err in errors:
                self.toast(err)
            return
        dialog = Adw.MessageDialog.new(
            self,
            "Install into PipeWire?",
            "A fragment will be written to ~/.config/pipewire/pipewire.conf.d/ "
            "with a timestamped backup of the previous one.  PipeWire is NOT "
            "restarted by this action - you decide when to restart it.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(d: Adw.MessageDialog, response: str) -> None:
            if response == "install":
                try:
                    plan = apply_mod.install_conf(text, apply_mod.default_conf_dir())
                except (OSError, ValueError) as exc:
                    self.toast(f"Install failed: {exc}")
                    d.destroy()
                    return
                self.toast("Installed - restart PipeWire yourself when ready")
                if plan.backup:
                    self.toast(f"Previous fragment backed up to {plan.backup.name}")
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def action_reset(self) -> None:
        self.controller.layout = type(self.controller.layout)(reference_mode="listener")
        self.canvas.select(None)
        self.controller.notify()
        self.toast("Layout reset")

    def action_about(self) -> None:
        about = Adw.AboutWindow.new()
        about.set_application_name("Monitor Layout")
        about.set_application_icon("audio-speakers-symbolic")
        about.set_version("0.1.0")
        about.set_developer("VariableThe")
        about.set_comments(
            "Arrange your displays and speakers, preview the channel mix and "
            "export a PipeWire routing configuration.  Your live audio is "
            "never changed automatically."
        )
        about.present()


class MonitorLayoutApp(Adw.Application):
    def __init__(
        self,
        demo: bool = False,
        fixtures_dir: Optional[Path] = None,
        controller: Optional[LayoutController] = None,
        application_id: str = APP_ID,
        initial_view: str = "front",
    ) -> None:
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        if initial_view not in ("front", "room"):
            raise ValueError("initial_view must be front or room")
        self.initial_view = initial_view
        self.demo = demo
        self.fixtures_dir = fixtures_dir or Path(__file__).resolve().parent.parent / "fixtures"
        self.initial_controller = controller

    def do_activate(self) -> None:
        controller = self.initial_controller
        if controller is None:
            controller = LayoutController()
            if self.demo:
                controller.load_demo(self.fixtures_dir)
            else:
                controller.discover_sinks()
        window = MonitorLayoutWindow(self, controller)
        window.view_drop.set_selected(1 if self.initial_view == "room" else 0)
        window.present()
