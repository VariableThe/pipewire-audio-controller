#!/usr/bin/env python3
"""Isolated, loopback-only GTK demo for visual QA (no audio).

Run with: dbus-run-session -- python3 scripts/preview_demo.py
Stop with Ctrl-C. Only subprocesses created here are terminated.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8731)
    parser.add_argument('--view', choices=('front', 'room'), default='front')
    parser.add_argument('--file', help='saved layout to inspect with fixture-only discovery')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='monitor-layout-preview-') as tmp:
        env = os.environ.copy()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE',
                    'SWAYSOCK', 'I3SOCK', 'PULSE_SERVER', 'PIPEWIRE_REMOTE', 'PIPEWIRE_RUNTIME_DIR'):
            env.pop(key, None)
        for key, suffix in (('XDG_RUNTIME_DIR','run'), ('XDG_CONFIG_HOME','config'),
                            ('XDG_CACHE_HOME','cache'), ('XDG_STATE_HOME','state')):
            p = Path(tmp) / suffix
            p.mkdir(mode=0o700)
            env[key] = str(p)
        env.update(GDK_BACKEND='broadway', BROADWAY_DISPLAY=':1', GSETTINGS_BACKEND='memory',
                   GTK_A11Y='none', NO_AT_BRIDGE='1', ADW_DISABLE_PORTAL='1', GTK_USE_PORTAL='0', GIO_USE_VFS='local')
        children = []
        try:
            daemon = subprocess.Popen(['gtk4-broadwayd','-a','127.0.0.1','-p',str(args.port),':1'], env=env)
            children.append(daemon)
            deadline = time.monotonic()+8
            while True:
                if daemon.poll() is not None:
                    raise RuntimeError('Preview daemon failed; port may be occupied')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/', timeout=.3):
                        break
                except OSError:
                    if time.monotonic()>deadline:
                        raise RuntimeError('Preview daemon did not become ready')
                    time.sleep(.1)
            command = [sys.executable, str(ROOT/'monitor-layout-gui.py'), '--demo', '--view', args.view]
            if args.file:
                command.extend(['--file', str(Path(args.file).resolve())])
            app = subprocess.Popen(command, cwd=ROOT, env=env)
            children.append(app)
            print(json.dumps({'url':f'http://127.0.0.1:{args.port}/','app_pid':app.pid,
                              'broadway_pid':daemon.pid,'state':tmp}),flush=True)
            # A forgotten QA session does not run indefinitely.
            until = time.monotonic()+3600
            while app.poll() is None and time.monotonic()<until:
                time.sleep(.25)
            return app.returncode or 0
        except KeyboardInterrupt:
            return 0
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()

if __name__ == '__main__':
    raise SystemExit(main())
