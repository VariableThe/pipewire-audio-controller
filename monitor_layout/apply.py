"""Guarded, explicit apply/rollback for generated configurations.

Design rules (and why the GUI does not call this):

* ``plan_install`` is pure: it describes and validates the operation.
* ``install_config`` writes the fragment into a pipewire.conf.d directory
  with a timestamped backup, then *prints* the restart commands for the user
  to run by hand.  It never restarts PipeWire or touches defaults itself.
* ``rollback_config`` restores the newest backup written by this tool.
* Both refuse to run unless explicitly invoked from the CLI.

Run manually, e.g.::

    python3 -m monitor_layout.apply install ~/.config/.../monitor-layout.conf
    python3 -m monitor_layout.apply rollback
"""

from __future__ import annotations

import shutil
import os
import tempfile
import uuid
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .routing import _static_check

FRAGMENT_NAME = "monitor-layout.conf"
BACKUP_PREFIX = "monitor-layout.conf.backup-"


def default_conf_dir() -> Path:
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pipewire" / "pipewire.conf.d"


RESTART_HINT = (
    "No running audio routing was changed. When ready, follow your distribution's "
    "instructions to reload/restart the user PipeWire services, which may interrupt audio. "
    "Then choose 'Monitor Layout Combined Sink' explicitly in sound settings. "
    "Do not guess a numeric output ID from text parsing."
)


@dataclass
class InstallPlan:
    target: Path
    backup: Path | None
    steps: list[str] = field(default_factory=list)


def plan_install(conf_text: str, conf_dir: Path) -> InstallPlan:
    """Read-only planning: validate text and inspect whether a backup is needed."""
    _static_check(conf_text)
    if not conf_text.strip():
        raise ValueError("refusing to install an empty configuration")
    target = Path(conf_dir) / FRAGMENT_NAME
    backup = None
    if target.exists():
        backup = target.with_name(f"{BACKUP_PREFIX}{time.time_ns()}-{uuid.uuid4().hex[:8]}")
    return InstallPlan(
        target=target,
        backup=backup,
        steps=[
            f"validate generated fragment ({len(conf_text)} bytes)",
            f"backup existing {target}" if backup else "no existing fragment",
            f"write {target}",
            "print restart instructions (user runs them manually)",
        ],
    )


def _execute_plan(plan: InstallPlan, conf_text: str) -> None:
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    if plan.backup is not None:
        shutil.copy2(plan.target, plan.backup)
    fd, tmp = tempfile.mkstemp(dir=plan.target.parent, prefix=".monitor-layout-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(conf_text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, plan.target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install_conf(conf_text: str, conf_dir: Path) -> InstallPlan:
    """Write the fragment, keeping a timestamped backup of the old one.

    Explicitly invoked by the user (CLI) or by the GUI's confirmed install
    action.  It never restarts PipeWire and never changes a default output.
    """
    plan = plan_install(conf_text, Path(conf_dir))
    _execute_plan(plan, conf_text)
    return plan


def _list_backups(conf_dir: Path) -> list[Path]:
    return sorted(Path(conf_dir).glob(BACKUP_PREFIX + "*"))


def rollback_config(conf_dir: Path) -> Path:
    """Restore the most recent backup over the current fragment."""
    conf_dir = Path(conf_dir)
    backups = _list_backups(conf_dir)
    if not backups:
        raise FileNotFoundError(f"no backups found in {conf_dir}")
    newest = backups[-1]
    text = newest.read_text(encoding="utf-8")
    _static_check(text)
    _execute_plan(InstallPlan(conf_dir / FRAGMENT_NAME, None), text)
    return newest


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("install", "rollback"):
        print("usage: apply.py install <conf-file> [--conf-dir DIR]")
        print("       apply.py rollback [--conf-dir DIR]")
        return 2
    action = argv[1]
    conf_dir = default_conf_dir()
    if "--conf-dir" in argv:
        conf_dir = Path(argv[argv.index("--conf-dir") + 1])
    if action == "install":
        if len(argv) < 3 or argv[2].startswith("--"):
            print("install requires the exported configuration path")
            return 2
        conf_text = Path(argv[2]).read_text(encoding="utf-8")
        plan = plan_install(conf_text, conf_dir)
        for step in plan.steps:
            print(f"* {step}")
        _execute_plan(plan, conf_text)
        print(f"\nInstalled {plan.target}.")
        if plan.backup:
            print(f"Backup of previous fragment: {plan.backup}")
        print("\nApply it manually (audio restarts only when YOU run this):\n")
        print(RESTART_HINT)
        return 0
    restored = rollback_config(conf_dir)
    print(f"Restored {restored} -> {conf_dir / FRAGMENT_NAME}")
    print("\nRestart manually when ready:\n")
    print(RESTART_HINT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
