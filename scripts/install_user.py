#!/usr/bin/env python3
"""Explicit user-local GUI installation. Never changes audio configuration."""
import os
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parent.parent
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
    target = data / "monitor-layout"
    bindir = home / ".local/bin"
    apps = data / "applications"
    for path in (target, bindir, apps):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("monitor_layout", "fixtures"):
        shutil.copytree(root / name, target / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(root / "monitor-layout-gui.py", target / "monitor-layout-gui.py")
    launcher = bindir / "monitor-layout-gui"
    launcher.write_text("#!/usr/bin/env python3\nimport os, sys\nos.execvp('python3', "
                        + repr(["python3", str(target / "monitor-layout-gui.py")])
                        + " + sys.argv[1:])\n")
    launcher.chmod(0o755)
    value = str(launcher).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    desktop = (root / "monitor-layout-gui.desktop").read_text()
    desktop = desktop.replace("Exec=monitor-layout-gui", 'Exec="' + value + '"')
    (apps / "monitor-layout-gui.desktop").write_text(desktop)
    print(f"Installed GUI to {target}; launcher: {launcher}")
    print("No PipeWire configuration was installed or activated.")

if __name__ == "__main__":
    main()
