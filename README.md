# PipeWire Audio Output Controller & Dual Monitor Setup

This repository contains the configurations, scripts, and a graphical user interface (GUI) designed to customize and manage your Linux audio output under **PipeWire** and **WirePlumber**. 

Specifically, it sets up:
1. A **custom 4-channel panning layout** that routes your stereo audio across a dual-monitor speaker setup.
2. A **GTK 4 / Libadwaita GUI application** to view outputs, control volumes, mute devices, and switch active playback targets.
3. A **dynamic bash script** to instantly toggle your system's default output between your USB DAC (IEMs) and your custom speaker setup (e.g., for binding to a keyboard hotkey).

---

## Repository Contents

*   **`dual-monitor.conf`**: PipeWire module configuration file that sets up virtual filter-chains and a combined audio sink.
*   **`audio-output-controller.py`**: A python-based GTK 4 & Libadwaita desktop GUI application.
*   **`toggle-audio.sh`**: A lightweight shell script that parses PipeWire IDs dynamically to toggle audio outputs.
*   **`audio-output-controller.desktop`**: The system desktop entry file to integrate the GUI app into your application menus and launchers (Rofi, Walker, etc.).

---

## 1. Dual Monitor Speaker Layout Setup

The `dual-monitor.conf` configuration maps standard stereo audio into a combined sink (`dual_monitor_sink`) which splits audio to your left and right monitors using a custom panning blend:
*   **Left Speaker (Left Monitor)**: Left channel only.
*   **Right Speaker (Left Monitor)**: Mono blend (Left + Right).
*   **Left Speaker (Right Monitor)**: Mono blend (Left + Right).
*   **Right Speaker (Right Monitor)**: Right channel only.

### Installation:
1. Create the configuration directory if it doesn't exist:
    ```bash
    mkdir -p ~/.config/pipewire/pipewire.conf.d/
    ```
2. Copy `dual-monitor.conf` into that directory:
    ```bash
    cp dual-monitor.conf ~/.config/pipewire/pipewire.conf.d/
    ```
3. Restart the PipeWire sound servers to load the new config:
    ```bash
    systemctl --user restart pipewire pipewire-pulse wireplumber
    ```

---

## 2. Desktop GUI Controller Application

The GUI provides a card-based view of all your hardware and virtual outputs (such as your USB DAC / IEMs and the custom `dual_monitor_sink`), showing active defaults, real-time volume sliders, and mute toggles.

### Dependencies:
Make sure python, GTK 4, Libadwaita, and PyGObject bindings are installed on your Linux system.
*   **Arch Linux / CachyOS**:
    ```bash
    sudo pacman -S python-gobject libadwaita
    ```
*   **Debian / Ubuntu**:
    ```bash
    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
    ```

### Installation:
1. Copy the script to your local bin path and make it executable:
    ```bash
    mkdir -p ~/.local/bin/
    cp audio-output-controller.py ~/.local/bin/audio-output-controller
    chmod +x ~/.local/bin/audio-output-controller
    ```
2. Copy the desktop entry shortcut to make it searchable in system launchers:
    ```bash
    mkdir -p ~/.local/share/applications/
    cp audio-output-controller.desktop ~/.local/share/applications/
    ```
3. Update the desktop entries database to register the app:
    ```bash
    update-desktop-database ~/.local/share/applications
    ```

---

## 3. Keyboard Shortcut Audio Toggle Script

The `toggle-audio.sh` script dynamically queries WirePlumber to find the current active output device and toggles default audio routing between your IEMs (USB DAC) and Speakers (`dual_monitor_sink`). Because it matches devices by name rather than arbitrary ID integers, it is robust and won't break if you plug/unplug your DAC.

### Setup:
1. Copy `toggle-audio.sh` into your bin folder and make it executable:
    ```bash
    cp toggle-audio.sh ~/.local/bin/toggle-audio
    chmod +x ~/.local/bin/toggle-audio
    ```
2. Run it at any time in terminal to switch outputs:
    ```bash
    toggle-audio
    ```
3. **Set a Hotkey:** You can bind this script to a media key or keyboard shortcut in your window manager.
    *   **Hyprland** (`hyprland.conf` example):
        ```ini
        # Toggle audio output between IEMs and speakers on a hotkey
        bind = $mainMod, Backspace, exec, ~/.local/bin/toggle-audio
        ```
    *   **i3 / Sway** (`config` example):
        ```text
        bindsym $mod+BackSpace exec ~/.local/bin/toggle-audio
        ```
