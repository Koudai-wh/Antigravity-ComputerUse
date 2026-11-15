"""Tests for launcher_gui.py and install_mcp_config.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from install_mcp_config import build_mcp_config_document, install_into_json_file


def test_mcp_config_generation_and_merge(tmp_path: Path) -> None:
    doc = build_mcp_config_document()
    entry = doc["mcpServers"]["antigravity-computer-use"]
    assert "computer_use_mcp.py" in entry["args"][0]
    assert Path(entry["args"][0]).exists()

    target = tmp_path / "claude_desktop_config.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "echo"}}}), encoding="utf-8")
    install_into_json_file(target)

    merged = json.loads(target.read_text(encoding="utf-8"))
    assert "other" in merged["mcpServers"]
    assert "antigravity-computer-use" in merged["mcpServers"]


def test_launcher_gui_constructs_under_display() -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    import tkinter as tk
    from launcher_gui import LauncherApp

    root = tk.Tk()
    app = LauncherApp(root, auto_start_overlay=False)
    assert app.var_base_url.get().startswith("http://")
    assert app.var_video_mode.get() is True
    assert app.var_target.get() in ("display", "screen_sharing", "active_window")
    app._on_close()
