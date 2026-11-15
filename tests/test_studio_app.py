"""Tests for Antigravity AI Studio UI, PiP Live Preview, File Output Export, Attachments, and Retina Coords."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import httpx
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from artifact_parser import extract_artifacts
from computer_use_mcp import AppRuntime
from gui_controller import CaptureRegion, CoordinateConverter
from studio_app import AntigravityStudioWindow, ArtifactCardWidget, ChatMessage
from vlm_client import fetch_available_models


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def test_artifact_parser_extracts_files_and_skips_tool_calls() -> None:
    sample = """
Here is your script:

```python:hello_mac.py
def greet(name: str) -> str:
    return f"Hello, {name}"
```

And a CSV export (`report.csv`):
```csv
name,score
alice,98
bob,95
```

<file name="summary.md">
# Summary
All tasks finished.
</file>

```json
{"name": "click", "arguments": {"x": 500, "y": 300}}
```
"""
    arts = extract_artifacts(sample)
    names = [a.filename for a in arts]
    assert "summary.md" in names
    assert "hello_mac.py" in names
    assert "report.csv" in names
    assert len(arts) == 3


def test_retina_backing_scale_coordinate_conversion() -> None:
    # Logical Mac Retina display: 1440x900, 2.0x backing pixels = 2880x1800
    region = CaptureRegion(
        left=0,
        top=0,
        width=1440,
        height=900,
        image_width=2880,
        image_height=1800,
        normalized_range=1000,
        backing_scale=2.0,
    )
    conv = CoordinateConverter(region)

    # Center in 2x image pixels -> logical center (720, 450)
    assert conv.to_pixels(1440, 900, "image_pixel") == (720, 450)
    # Center in 2x backing pixels -> logical center (720, 450)
    assert conv.to_pixels(1440, 900, "backing_pixel") == (720, 450)
    # Normalized center -> logical center (~720, 450)
    nx_px, nx_py = conv.to_pixels(500, 500, "normalized")
    assert abs(nx_px - 720) <= 1
    assert abs(nx_py - 450) <= 1


def test_fetch_available_models_normalizes_url(monkeypatch: object) -> None:
    captured_urls: list[str] = []

    def fake_get(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> httpx.Response:
        captured_urls.append(url)
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "gemma-4-12b"}, {"id": "DeepSeek-R1-Distill"}]},
        )

    monkeypatch.setattr(httpx, "get", fake_get)  # type: ignore[attr-defined]
    ok, models, _msg = fetch_available_models("http://100.121.235.97:8000")
    assert ok is True
    assert captured_urls == ["http://100.121.235.97:8000/v1/models"]
    assert models == ["gemma-4-12b", "DeepSeek-R1-Distill"]


def test_studio_window_pip_attachments_export_and_emergency_stop(tmp_path: Path) -> None:
    _get_qapp()
    cfg_copy = tmp_path / "config.json"
    cfg_copy.write_text((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"), encoding="utf-8")

    win = AntigravityStudioWindow(
        config_path=cfg_copy,
        dry_run=True,
        auto_fetch_models=False,
        enable_resident_overlay=False,
        overlay_backend="headless",
    )
    win.show()
    _get_qapp().processEvents()
    try:
        # 1. Model dropdown population
        win._on_models_fetched(True, ["gemma-4-12b", "Qwen2.5-VL-7B"], "Connected (2 models)")
        assert win.model_combo.count() == 2
        assert win.model_combo.currentText() == "gemma-4-12b"

        # 2. Mode switching
        win.set_mode("computer_use")
        assert win._mode == "computer_use"
        assert not win.target_badge.isHidden()
        win.set_mode("chat")
        assert win._mode == "chat"
        assert win.target_badge.isHidden()

        # 3. Attachments (PNG + Python file) & multimodal payload building
        img_file = tmp_path / "sample.png"
        Image.new("RGB", (64, 48), color=(30, 144, 255)).save(img_file, format="PNG")
        py_file = tmp_path / "notes.py"
        py_file.write_text("print('hi')\n", encoding="utf-8")

        added = win.add_attachments([str(img_file), str(py_file)])
        assert added == 2
        assert len(win._attachments) == 2

        conv = win._get_active_conversation()
        conv.messages.append(
            ChatMessage(role="user", text="Analyze these", attachments=list(win._attachments), mode="chat")
        )
        api_msgs = win._build_openai_chat_messages(conv)
        user_payload = api_msgs[-1]["content"]
        assert isinstance(user_payload, list)
        assert any(p.get("type") == "image_url" for p in user_payload)
        assert any("notes.py" in p.get("text", "") for p in user_payload if p.get("type") == "text")

        # 4. File Output Card rendering & Export
        conv.messages.append(
            ChatMessage(
                role="assistant",
                text="Done:\n```python:exported_tool.py\nVALUE = 42\n```\n",
                mode="chat",
            )
        )
        win._render_messages()
        cards = win.chat_contents.findChildren(ArtifactCardWidget)
        assert len(cards) == 1
        assert cards[0].artifact.filename == "exported_tool.py"
        out_path = tmp_path / "saved" / "exported_tool.py"
        cards[0].export_to_path(out_path)
        assert out_path.read_text(encoding="utf-8") == "VALUE = 42"

        # 5. PiP Live Preview frame, cursor, click ripple, collapse & close
        buf = io.BytesIO()
        Image.new("RGB", (320, 200), color=(24, 24, 30)).save(buf, format="PNG")
        win.pip_preview.show_live(expand=True)
        win._position_floating_widgets()
        assert win.pip_preview.isVisible()
        assert win.pip_preview.x() > win.chat_page.width() // 2
        assert win.pip_preview.y() <= 20
        win._bridge.gui_event.emit(
            "screenshot",
            {"bytes": buf.getvalue(), "target": "display", "active_app": "Finder"},
        )
        win._bridge.gui_event.emit("move", {"nx": 420.0, "ny": 310.0, "label": "Moving..."})
        win._bridge.gui_event.emit(
            "click",
            {"nx": 420.0, "ny": 310.0, "button": "left", "clicks": 1, "label": "Clicking (420, 310)..."},
        )
        win.pip_preview.set_agent_status("thinking", "Thinking (gemma-4-12b)...")
        _get_qapp().processEvents()
        win.pip_preview.canvas.repaint()

        win.pip_preview.toggle_collapsed()
        assert win.pip_preview.is_collapsed is True
        assert win.pip_preview.height() == 34
        win.pip_preview.toggle_collapsed()
        assert win.pip_preview.is_collapsed is False
        assert win.pip_preview.height() == 234
        win.pip_preview.close_preview()
        assert not win.pip_preview.isVisible()

        # 6. Emergency Stop via Esc double-tap eventFilter & resume
        ev1 = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        ev2 = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        win.eventFilter(win, ev1)
        assert win.runtime.safety.state.paused is False
        win.eventFilter(win, ev2)
        assert win.runtime.safety.state.paused is True
        assert win.runtime.safety.state.emergency_stopped is True
        win.toggle_emergency_stop()
        assert win.runtime.safety.state.paused is False
    finally:
        win.close()
