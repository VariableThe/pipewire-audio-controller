import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "fixtures"


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def broadway_display():
    """GUI tests run only inside scripts/run_gui_tests.py's private display."""
    assert os.environ.get("PW_GUI_TESTS") == "1", "Use python3 scripts/run_gui_tests.py"
    assert os.environ.get("GDK_BACKEND") == "broadway"
    assert os.environ.get("XDG_RUNTIME_DIR") == os.environ.get("PW_GUI_TEST_RUNTIME")
    from gi.repository import Gdk
    assert Gdk.Display.get_default() is not None, "Isolated Broadway display failed"
    return os.environ["BROADWAY_DISPLAY"]
