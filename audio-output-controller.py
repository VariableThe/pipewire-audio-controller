#!/usr/bin/env python3
import sys
import subprocess
import re
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

class AudioControllerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.cachyos.AudioController",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self):
        win = AudioControllerWindow(self)
        win.present()

class AudioControllerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Audio Output Controller")
        self.set_default_size(440, 520)
        
        # Load custom CSS to style active cards
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .active-row {
                background-color: alpha(@accent_color, 0.12);
                border: 2px solid @accent_color;
            }
            .device-card {
                margin: 6px 12px;
                padding: 12px;
                border-radius: 12px;
                background-color: @card_bg_color;
                border: 1px solid @card_border_color;
            }
            .vol-slider {
                margin-left: 8px;
                margin-right: 8px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Main Layout Box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)

        # Header Bar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Refresh button in header
        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh device list")
        refresh_btn.connect("clicked", lambda x: self.refresh_devices())
        header.pack_start(refresh_btn)

        # Scrollable container for the device list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        main_box.append(scrolled)

        # List box container inside scrollable
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.set_valign(Gtk.Align.START)
        scrolled.set_child(self.list_box)

        self.device_widgets = {}

        # Render first view
        self.refresh_devices()

        # Refresh states every 2 seconds in the background
        GLib.timeout_add_seconds(2, self.periodic_refresh)

    def get_audio_sinks(self):
        try:
            output = subprocess.check_output(["wpctl", "status"], text=True)
        except Exception:
            return []

        sections = {}
        current_section = None
        in_audio = False

        for line in output.splitlines():
            if line.strip() == "Audio":
                in_audio = True
                continue
            elif line.strip() in ("Video", "Settings"):
                in_audio = False
                continue

            if in_audio:
                if "├─" in line or "└─" in line:
                    header_match = re.search(r"(?:├─|└─)\s*([^:]+):", line)
                    if header_match:
                        current_section = header_match.group(1).strip()
                        sections[current_section] = []
                        continue
                if current_section and line.strip():
                    sections[current_section].append(line)

        sinks = []
        # Parse Sinks
        if "Sinks" in sections:
            for line in sections["Sinks"]:
                line = re.sub(r"^[│\s├─└─\s]*", "", line).strip()
                match = re.match(r"^(\*?\s*)(\d+)\.\s+(.*?)(?:\s+\[.*\])?$", line)
                if match:
                    sinks.append({
                        "id": int(match.group(2)),
                        "name": match.group(3).strip(),
                        "is_default": "*" in match.group(1),
                        "type": "Hardware"
                    })

        # Parse Filters
        if "Filters" in sections:
            for line in sections["Filters"]:
                line = re.sub(r"^[│\s├─└─\s]*", "", line).strip()
                if "[Audio/Sink]" in line:
                    line_clean = line.replace("[Audio/Sink]", "").strip()
                    match = re.match(r"^(\*?\s*)(\d+)\.\s+(.*?)$", line_clean)
                    if match:
                        sinks.append({
                            "id": int(match.group(2)),
                            "name": match.group(3).strip(),
                            "is_default": "*" in match.group(1),
                            "type": "Virtual"
                        })

        # Query volume/mute for each sink
        for sink in sinks:
            try:
                vol_out = subprocess.check_output(["wpctl", "get-volume", str(sink["id"])], text=True).strip()
                vol_match = re.search(r"Volume:\s*([\d\.]+)", vol_out)
                sink["volume"] = float(vol_match.group(1)) if vol_match else 1.0
                sink["muted"] = "[MUTED]" in vol_out
            except Exception:
                sink["volume"] = 1.0
                sink["muted"] = False

        return sinks

    def refresh_devices(self):
        sinks = self.get_audio_sinks()
        
        # Clear existing widgets
        while True:
            child = self.list_box.get_first_child()
            if child is None:
                break
            self.list_box.remove(child)
            
        self.device_widgets.clear()

        # Build list
        for sink in sinks:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("device-card")
            if sink["is_default"]:
                card.add_css_class("active-row")

            # Row for Name and Set-Default / Active status
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            card.append(top_row)

            # Icon
            is_iem = "DAC" in sink["name"] or "headphone" in sink["name"].lower() or "iem" in sink["name"].lower()
            icon_name = "audio-headphones-symbolic" if is_iem else "audio-speakers-symbolic"
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_icon_size(Gtk.IconSize.LARGE)
            top_row.append(icon)

            # Info labels (Name & Type)
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info_box.set_hexpand(True)
            top_row.append(info_box)

            name_lbl = Gtk.Label(label=sink["name"])
            name_lbl.set_halign(Gtk.Align.START)
            name_lbl.set_wrap(True)
            name_lbl.set_max_width_chars(35)
            name_lbl.add_css_class("heading")
            name_lbl.add_css_class("bold")
            info_box.append(name_lbl)

            type_lbl = Gtk.Label(label=f"{sink['type']} Output (ID: {sink['id']})")
            type_lbl.set_halign(Gtk.Align.START)
            type_lbl.add_css_class("caption")
            type_lbl.add_css_class("dim-label")
            info_box.append(type_lbl)

            # Action Button (Active checkmark or Activate button)
            if sink["is_default"]:
                active_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                active_icon.set_tooltip_text("Active Default Device")
                top_row.append(active_icon)
            else:
                set_default_btn = Gtk.Button(label="Activate")
                set_default_btn.add_css_class("pill")
                set_default_btn.connect("clicked", self.on_set_default, sink["id"])
                top_row.append(set_default_btn)

            # Row for Volume Slider and Mute Toggle
            vol_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            vol_row.set_margin_top(8)
            card.append(vol_row)

            # Mute toggle button
            mute_icon = "audio-volume-muted-symbolic" if sink["muted"] else "audio-volume-high-symbolic"
            mute_btn = Gtk.Button.new_from_icon_name(mute_icon)
            mute_btn.connect("clicked", self.on_toggle_mute, sink["id"])
            vol_row.append(mute_btn)

            # Volume slider
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            slider.set_value(sink["volume"] * 100)
            slider.set_draw_value(False)
            slider.set_hexpand(True)
            slider.add_css_class("vol-slider")
            slider.connect("value-changed", self.on_slider_changed, sink["id"])
            vol_row.append(slider)

            # Percentage label
            pct_lbl = Gtk.Label(label=f"{int(sink['volume']*100)}%")
            pct_lbl.set_size_request(35, -1)
            vol_row.append(pct_lbl)

            self.list_box.append(card)
            self.device_widgets[sink["id"]] = {
                "card": card,
                "slider": slider,
                "pct_lbl": pct_lbl,
                "mute_btn": mute_btn
            }

    def periodic_refresh(self):
        sinks = self.get_audio_sinks()
        # If the device list itself changed, rebuild the GUI
        current_ids = set(self.device_widgets.keys())
        new_ids = set(s['id'] for s in sinks)
        
        if current_ids != new_ids:
            self.refresh_devices()
            return True

        for sink in sinks:
            widgets = self.device_widgets.get(sink["id"])
            if not widgets:
                continue

            # Update active class and default state
            if sink["is_default"]:
                if not widgets["card"].has_css_class("active-row"):
                    self.refresh_devices()
                    return True
            else:
                widgets["card"].remove_css_class("active-row")

            # Update slider and label if user is not actively dragging it
            if not widgets["slider"].has_focus():
                widgets["slider"].set_value(sink["volume"] * 100)
                widgets["pct_lbl"].set_label(f"{int(sink['volume']*100)}%")

            # Update mute icon
            mute_icon = "audio-volume-muted-symbolic" if sink["muted"] else "audio-volume-high-symbolic"
            widgets["mute_btn"].set_icon_name(mute_icon)

        return True

    def on_set_default(self, button, sink_id):
        subprocess.run(["wpctl", "set-default", str(sink_id)])
        self.refresh_devices()

    def on_toggle_mute(self, button, sink_id):
        subprocess.run(["wpctl", "set-mute", str(sink_id), "toggle"])
        self.refresh_devices()

    def on_slider_changed(self, scale, sink_id):
        val = scale.get_value()
        widgets = self.device_widgets.get(sink_id)
        if widgets:
            widgets["pct_lbl"].set_label(f"{int(val)}%")
        subprocess.run(["wpctl", "set-volume", str(sink_id), f"{int(val)}%"])

if __name__ == "__main__":
    app = AudioControllerApp()
    sys.exit(app.run(sys.argv))
