"""scripts/preview_update_button.py opens the real window with a pretend update, and saves a picture."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preview_update_button.py"


def test_the_script_saves_a_picture_of_the_window_with_the_button(tmp_path):
    out = tmp_path / "bar.png"
    env = {
        **os.environ,
        "DUBCRATE_DATA_DIR": str(tmp_path / "data"),
        "QT_QPA_PLATFORM": "offscreen",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--theme", "dark", "--version", "0.3.0", "--screenshot", str(out)],
        check=False, capture_output=True, text=True, env=env, timeout=120,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert out.is_file() and out.stat().st_size > 5000
    assert "saved" in result.stdout
