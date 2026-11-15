"""Tests for coordinate conversion, app allowlist guardrails, kill switch, and human-priority lock."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import SafetyConfig
from gui_controller import CaptureRegion, CoordinateConverter
from safety import AppPermissionError, SafetyAbortError, SafetyMonitor


def test_coordinate_converter_normalized_and_multimonitor() -> None:
    region = CaptureRegion(
        left=1920,
        top=100,
        width=2560,
        height=1440,
        image_width=1280,
        image_height=720,
        normalized_range=1000,
    )
    conv = CoordinateConverter(region)

    # Top-left & bottom-right normalized
    assert conv.to_pixels(0, 0, "normalized") == (1920, 100)
    assert conv.to_pixels(1000, 1000, "normalized") == (1920 + 2559, 100 + 1439)

    # Center normalized
    cx, cy = conv.to_pixels(500, 500, "normalized")
    assert abs(cx - (1920 + 1280)) <= 1
    assert abs(cy - (100 + 720)) <= 1

    # Round trip back to normalized (quantization error <= 1 pixel)
    nx, ny = conv.to_normalized(cx, cy)
    assert abs(nx - 500.0) < 1.0
    assert abs(ny - 500.0) < 1.0

    # Image pixel scaling (1280x720 screenshot -> 2560x1440 monitor)
    assert conv.to_pixels(640, 360, "image_pixel") == (1920 + 1280, 100 + 720)

    # Absolute desktop pixel pass-through
    assert conv.to_pixels(300, 400, "pixel") == (300, 400)


def test_app_allowlist_and_blocklist() -> None:
    persisted: list[str] = []
    prompts: list[str] = []

    def confirm(app_name: str, _timeout: float) -> str:
        prompts.append(app_name)
        if app_name == "Slack":
            return "always_allow"
        if app_name == "PreviewApp":
            return "allow_once"
        return "deny"

    cfg = SafetyConfig(
        enforce_app_allowlist=True,
        unlisted_app_policy="prompt",
        always_allowed_apps=["Google Chrome", "notepad.exe", "Finder"],
        blocked_apps=["1Password", "Terminal", "cmd.exe"],
    )
    mon = SafetyMonitor(cfg, confirm_callback=confirm, persist_allow_callback=persisted.append)

    # Allowed (case / extension normalized)
    assert mon.check_app_allowed("Finder.app")[0] is True
    assert mon.check_app_allowed("Notepad")[0] is True
    assert mon.check_app_allowed("Google Chrome")[0] is True

    # Blocked
    assert mon.check_app_allowed("Terminal")[0] is False
    assert mon.check_app_allowed("1Password")[0] is False
    with pytest.raises(AppPermissionError):
        mon.require_app_allowed("cmd.exe")

    # Prompt: always_allow
    ok, _ = mon.check_app_allowed("Slack")
    assert ok is True
    assert "Slack" in persisted
    assert mon.check_app_allowed("Slack")[0] is True

    # Prompt: allow_once
    ok, _ = mon.check_app_allowed("PreviewApp")
    assert ok is True
    assert "PreviewApp" not in persisted

    # Prompt: deny
    ok, _ = mon.check_app_allowed("UnknownTool")
    assert ok is False
    assert prompts == ["Slack", "PreviewApp", "UnknownTool"]


def test_kill_switch_double_esc_and_hotkey() -> None:
    paused_reasons: list[str] = []
    cfg = SafetyConfig()
    mon = SafetyMonitor(cfg, on_pause_callback=paused_reasons.append)

    # Even during an active AI action, single Esc does not trigger emergency stop,
    # while double Esc within 1.2s IMMEDIATELY triggers emergency kill switch!
    with pytest.raises(SafetyAbortError):
        with mon.ai_action_guard(500, 500):
            mon.handle_key_press("esc", now=10.0)
            mon.ensure_can_act()
            mon.handle_key_press("esc", now=10.4)
            mon.ensure_can_act()

    assert mon.state.paused is True
    assert mon.state.emergency_stopped is True
    assert paused_reasons[-1] == "kill_switch_esc"

    # Resume and test emergency hotkey (ctrl+alt+shift+x)
    mon.resume()
    mon.ensure_can_act()
    mon.handle_key_press("ctrl", now=20.0)
    mon.handle_key_press("alt", now=20.05)
    mon.handle_key_press("shift", now=20.1)
    mon.handle_key_press("x", now=20.15)
    assert mon.state.emergency_stopped is True
    assert mon.state.pause_reason == "kill_switch_hotkey"


def test_human_priority_lock_and_ai_suppression() -> None:
    cfg = SafetyConfig()
    cfg.human_priority_lock.mouse_move_threshold_px = 20
    mon = SafetyMonitor(cfg)

    # AI moves inside ai_action_guard -> must NOT trigger pause
    with mon.ai_action_guard(500, 500):
        mon.handle_mouse_move(505, 502)
        mon.handle_mouse_click(500, 500)
        mon.ensure_can_act()

    # Human suddenly grabs mouse during an AI action (> threshold jump) -> MUST abort!
    with pytest.raises(SafetyAbortError):
        with mon.ai_action_guard(500, 500):
            mon.handle_mouse_move(100, 100)

    mon.resume()

    # Wait until post-action suppress window expires, then simulate human keypress -> MUST pause!
    mon._suppress_until = 0.0
    mon.handle_key_press("a")
    assert mon.state.paused is True
    assert "human_key_press" in (mon.state.pause_reason or "")
