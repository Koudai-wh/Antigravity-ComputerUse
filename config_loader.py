"""Configuration loader and Pydantic schemas for Antigravity Computer Use."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


class ServerConfig(BaseModel):
    name: str = "antigravity-computer-use"
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765


class VLMConfig(BaseModel):
    base_url: str = "http://100.121.235.97:8000/v1"
    api_key: str = "EMPTY"
    model: str = "gemma-4-12b"
    model_preset: str = "gemma4"
    coordinate_system: Literal["normalized_1000", "pixel"] = "normalized_1000"
    max_tokens: int = 1024
    temperature: float = 0.1
    timeout_seconds: float = 60.0
    video_mode: bool = True
    video_frames: int = 3
    video_interval_sec: float = 0.25
    video_max_width: int = 1280
    capture_target: Literal["display", "active_window", "screen_sharing"] = "display"


class DisplayConfig(BaseModel):
    default_display_index: int = 1
    screenshot_format: Literal["png", "jpeg"] = "png"
    jpeg_quality: int = 85
    max_screenshot_width: int | None = 1920
    normalized_range: int = 1000


class ClickEffectConfig(BaseModel):
    enabled: bool = True
    ripple_duration_ms: int = 450
    ripple_max_radius: int = 38
    left_color: str = "#00D4FF"
    right_color: str = "#FF9500"
    double_color: str = "#7C4DFF"
    scale_pulse: float = 0.82


class OverlayConfig(BaseModel):
    enabled: bool = True
    always_visible: bool = True
    hide_during_screenshot: bool = False
    exclude_from_capture: bool = True
    cursor_image_path: str = "assets/virtual_cursor.png"
    cursor_size: int = 48
    hotspot_ratio_x: float = 0.789
    hotspot_ratio_y: float = 0.211
    smooth_move: bool = True
    move_duration_sec: float = 0.35
    fps: int = 60
    opacity: float = 0.96
    show_label: bool = True
    label_text: str = "AI"
    thinking_spinner_color: str = "#38BDF8"
    thinking_spinner_accent: str = "#A855F7"
    click_effect: ClickEffectConfig = Field(default_factory=ClickEffectConfig)
    idle_fade_seconds: float = 0.0

    def resolve_cursor_path(self, base_dir: Path = PROJECT_ROOT) -> Path:
        p = Path(self.cursor_image_path)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        return p


class KillSwitchConfig(BaseModel):
    enabled: bool = True
    esc_press_count: int = 2
    esc_window_sec: float = 1.2
    emergency_hotkey: list[str] = Field(default_factory=lambda: ["ctrl", "alt", "shift", "x"])


class HumanPriorityLockConfig(BaseModel):
    enabled: bool = True
    mouse_move_threshold_px: int = 24
    auto_resume_after_idle_sec: float = 0.0


class SafetyConfig(BaseModel):
    enforce_app_allowlist: bool = True
    unlisted_app_policy: Literal["prompt", "block", "allow"] = "prompt"
    confirmation_timeout_sec: float = 20.0
    always_allowed_apps: list[str] = Field(default_factory=list)
    blocked_apps: list[str] = Field(default_factory=list)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    human_priority_lock: HumanPriorityLockConfig = Field(default_factory=HumanPriorityLockConfig)


class ActionsConfig(BaseModel):
    action_delay_sec: float = 0.05
    clipboard_restore: bool = True
    paste_delay_sec: float = 0.08
    scroll_multiplier: int = 120


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    actions: ActionsConfig = Field(default_factory=ActionsConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        env_path = os.environ.get("COMPUTER_USE_CONFIG")
        cfg_path = Path(path or env_path or DEFAULT_CONFIG_PATH)
        if not cfg_path.exists():
            return cls()
        raw_text = cfg_path.read_text(encoding="utf-8")
        if cfg_path.suffix.lower() == ".toml":
            import tomllib

            data: dict[str, Any] = tomllib.loads(raw_text)
        else:
            data = json.loads(raw_text)
        return cls.model_validate(data)

    def save(self, path: str | Path | None = None) -> Path:
        cfg_path = Path(path or DEFAULT_CONFIG_PATH)
        cfg_path.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return cfg_path
