"""monitor_layout - geometry, panning and routing core for the monitor-speaker
layout GUI.

Everything in this package except :mod:`monitor_layout.gui` is free of GTK /
display imports and performs no live audio interaction. Discovery helpers run
read-only commands only.
"""

__version__ = "0.1.0"

SCHEMA_VERSION = 3
