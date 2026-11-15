"""Unit & visual integration tests for the virtual cursor overlay (assets/virtual_cursor.png).

Can be run with pytest or directly as a standalone test script:
    python tests/test_cursor_overlay_visual.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import AppConfig
from cursor_overlay import (
    CursorOverlayClient,
    HeadlessOverlayBackend,
    create_overlay_backend,
    ease_in_out_cubic,
)


def test_virtual_cursor_asset_alpha_channel() -> None:
    cfg = AppConfig.load().overlay
    cursor_path = cfg.resolve_cursor_path(PROJECT_ROOT)
    assert cursor_path.exists(), f"Missing virtual cursor asset: {cursor_path}"

    im = Image.open(cursor_path)
    assert im.mode == "RGBA", f"Expected RGBA image with alpha channel, got {im.mode}"
    # Corner must be transparent
    assert im.getpixel((0, 0))[3] == 0
    # Hotspot vicinity must contain opaque/semi-opaque pointer tip pixels
    hx = int(round(im.width * cfg.hotspot_ratio_x))
    hy = int(round(im.height * cfg.hotspot_ratio_y))
    patch = im.crop((max(0, hx - 20), max(0, hy - 20), min(im.width, hx + 20), min(im.height, hy + 20)))
    assert patch.getchannel("A").getextrema()[1] > 200


def test_easing_bounds() -> None:
    assert ease_in_out_cubic(0.0) == 0.0
    assert ease_in_out_cubic(1.0) == 1.0
    assert abs(ease_in_out_cubic(0.5) - 0.5) < 1e-6


def test_headless_overlay_move_and_click_render() -> None:
    cfg = AppConfig.load().overlay
    backend = HeadlessOverlayBackend(cfg)
    backend.move_to(320, 240, duration=0.05, wait=True)
    assert backend.get_state()["x"] == 320.0
    assert backend.get_state()["y"] == 240.0

    backend.trigger_click(320, 240, button="left", clicks=1)
    backend.trigger_click(500, 360, button="right", clicks=1)
    backend.trigger_click(620, 420, button="left", clicks=2)
    assert len(backend.state.ripples) == 3

    frame = backend.render_preview_frame(800, 600)
    assert frame.size == (800, 600)
    # Verify non-uniform pixels around the cursor/ripple location
    crop = frame.crop((570, 370, 670, 470))
    colors = crop.getcolors(maxcolors=20000)
    assert colors is not None and len(colors) > 5


def test_pyside6_offscreen_overlay_move_and_click() -> None:
    """Verify PySide6 QWidget overlay initializes, moves smoothly, and renders click ripples."""
    env_backup = os.environ.get("QT_QPA_PLATFORM")
    # Use offscreen if no X11/xcb-cursor0 is available
    if sys.platform.startswith("linux") and not env_backup:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        cfg = AppConfig.load().overlay
        overlay = create_overlay_backend(cfg, backend="pyside6")
        assert overlay.backend_name == "pyside6"
        overlay.move_to(250, 180, duration=0.08, wait=True)
        st = overlay.get_state()
        assert abs(st["x"] - 250.0) < 2.0
        assert abs(st["y"] - 180.0) < 2.0

        overlay.trigger_click(250, 180, button="left", clicks=1)
        overlay.trigger_click(420, 310, button="left", clicks=2)
        overlay.set_status("paused")
        assert overlay.get_state()["status"] == "paused"
        overlay.close()
    finally:
        if env_backup is None:
            os.environ.pop("QT_QPA_PLATFORM", None)
        else:
            os.environ["QT_QPA_PLATFORM"] = env_backup


def test_ipc_overlay_client_move_and_click() -> None:
    """Spawn cursor_overlay.py --serve over IPC, move cursor, trigger clicks, and shut down cleanly."""
    cfg = AppConfig.load().overlay
    cfg.move_duration_sec = 0.08
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or sys.platform in ("darwin", "win32"))
    backend = "auto" if has_display else "headless"

    client = CursorOverlayClient(cfg, backend=backend)
    try:
        info = client.start()
        assert info.get("ok") is True, f"Overlay failed to start: {info}"

        r1 = client.move_to(240, 200, duration=0.08, wait=True)
        assert r1.get("ok") is True
        assert abs(r1["state"]["x"] - 240.0) < 3.0
        assert abs(r1["state"]["y"] - 200.0) < 3.0

        r2 = client.trigger_click(240, 200, button="left", clicks=1)
        assert r2.get("ok") is True

        r3 = client.move_to(640, 420, duration=0.08, wait=True)
        assert r3.get("ok") is True
        assert abs(r3["state"]["x"] - 640.0) < 3.0

        r4 = client.trigger_click(640, 420, button="right", clicks=1)
        assert r4.get("ok") is True

        r5 = client.trigger_click(480, 320, button="left", clicks=2)
        assert r5.get("ok") is True
        assert abs(r5["state"]["x"] - 480.0) < 3.0

        client.set_status("paused")
        st = client.get_state()
        assert st["state"]["status"] == "paused"
    finally:
        client.close()


if __name__ == "__main__":
    print("Running Virtual Cursor Overlay standalone tests...")
    test_virtual_cursor_asset_alpha_channel()
    print("[PASS] virtual_cursor.png RGBA alpha & hotspot check")
    test_easing_bounds()
    print("[PASS] easing math check")
    test_headless_overlay_move_and_click_render()
    print("[PASS] headless overlay move/click/render check")
    test_pyside6_offscreen_overlay_move_and_click()
    print("[PASS] PySide6 overlay move/click/ripple check")
    test_ipc_overlay_client_move_and_click()
    print("[PASS] IPC subprocess overlay move/click/status check")
    print("All virtual cursor tests passed!")
