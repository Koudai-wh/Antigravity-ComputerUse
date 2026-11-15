"""MCP Server for Antigravity Computer Use (OpenAI Computer Use & Local VLM compatible).

Exposes desktop visual perception, mouse/keyboard/clipboard actions, app focus
control, virtual cursor visual overlay synchronization, and safety guardrails via
Model Context Protocol (MCP).
"""

from __future__ import annotations

import argparse
import atexit
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from mcp.types import ImageContent, TextContent

try:
    from mcp.server.mcpserver import MCPServer as _BaseMCPServer
except ImportError:  # pragma: no cover - mcp 1.x fallback
    from mcp.server.fastmcp import FastMCP as _BaseMCPServer  # type: ignore

from config_loader import DEFAULT_CONFIG_PATH, AppConfig
from cursor_overlay import CursorOverlayClient
from gui_controller import CoordMode, GUIController
from safety import AppPermissionError, SafetyAbortError, SafetyMonitor, check_os_permissions


class AppRuntime:
    """Holds live controller, safety monitor, and virtual cursor overlay instances."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        enable_overlay: bool = True,
        enable_listeners: bool = True,
        dry_run: bool = False,
        overlay_backend: str = "auto",
    ) -> None:
        self.config_path = Path(config_path or DEFAULT_CONFIG_PATH)
        self.config = AppConfig.load(self.config_path)
        if not enable_overlay:
            self.config.overlay.enabled = False

        self.overlay = CursorOverlayClient(self.config.overlay, backend=overlay_backend)
        self.safety = SafetyMonitor(
            self.config.safety,
            on_pause_callback=self._on_safety_pause,
            persist_allow_callback=self._persist_allowed_app,
        )
        self.controller = GUIController(
            self.config,
            self.safety,
            overlay=self.overlay,
            dry_run=dry_run,
        )
        self._enable_listeners = enable_listeners
        self._started = False

    def _on_safety_pause(self, reason: str) -> None:
        try:
            if self.config.overlay.enabled:
                self.overlay.set_status("paused")
        except Exception:
            pass

    def _persist_allowed_app(self, app_name: str) -> None:
        if app_name not in self.config.safety.always_allowed_apps:
            self.config.safety.always_allowed_apps.append(app_name)
            try:
                self.config.save(self.config_path)
            except Exception:
                pass

    def start(self) -> None:
        if self._started:
            return
        if self.config.overlay.enabled:
            self.overlay.start()
        if self._enable_listeners:
            self.safety.start_listeners()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.safety.stop_listeners()
        self.overlay.close()
        self._started = False


def _json_text(data: dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))


def _error_payload(exc: Exception) -> list[TextContent]:
    code = "error"
    if isinstance(exc, SafetyAbortError):
        code = "safety_paused"
    elif isinstance(exc, AppPermissionError):
        code = "app_not_allowed"
    return [_json_text({"ok": False, "error_type": code, "message": str(exc)})]


def create_mcp_server(runtime: AppRuntime | None = None) -> tuple[_BaseMCPServer, AppRuntime]:
    """Build and configure the MCPServer instance with all Computer Use tools."""
    rt = runtime or AppRuntime()

    @asynccontextmanager
    async def _lifespan(_server: Any) -> AsyncIterator[dict[str, Any]]:
        rt.start()
        try:
            yield {"runtime": rt}
        finally:
            rt.stop()

    mcp = _BaseMCPServer(
        name=rt.config.server.name,
        instructions=(
            "Desktop Computer Use MCP server. Use `take_screenshot` first to inspect the screen. "
            "Coordinates default to `normalized` (0-1000 scale across the captured screen/window). "
            "Every move/click displays an AI virtual cursor overlay for human visibility. "
            "If `safety_paused` occurs, stop acting and ask the user or call `resume_automation` once cleared."
        ),
        lifespan=_lifespan,
    )

    @mcp.tool()
    def take_screenshot(
        display_index: int | None = None,
        target: Literal["display", "active_window"] = "display",
        image_format: Literal["png", "jpeg"] | None = None,
        max_width: int | None = None,
        include_cursor: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Capture a screenshot of the specified display or active window and return Base64 image + metadata.

        Args:
            display_index: Monitor index (0=all monitors, 1=primary, 2+=secondary). Defaults to config.
            target: 'display' for full monitor capture or 'active_window' for focused window bounds.
            image_format: 'png' or 'jpeg'.
            max_width: Optional max pixel width for downscaling before sending to VLM.
            include_cursor: If False, hides the virtual cursor overlay during capture.
        """
        rt.start()
        try:
            res = rt.controller.take_screenshot(
                display_index=display_index,
                target=target,
                image_format=image_format,
                max_width=max_width,
                include_cursor=include_cursor,
            )
            meta = {"ok": True, **res["metadata"], "mime_type": res["mime_type"]}
            return [
                _json_text(meta),
                ImageContent(type="image", data=res["base64"], mime_type=res["mime_type"]),
            ]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def move_cursor(
        x: float,
        y: float,
        coord_mode: CoordMode = "normalized",
        duration: float | None = None,
    ) -> list[TextContent]:
        """Smoothly move both the virtual cursor overlay and system cursor to (x, y).

        Args:
            x: X coordinate (0-1000 when coord_mode='normalized', or pixels).
            y: Y coordinate (0-1000 when coord_mode='normalized', or pixels).
            coord_mode: 'normalized' (0-1000), 'image_pixel', or 'pixel'.
            duration: Optional movement duration in seconds.
        """
        rt.start()
        try:
            res = rt.controller.move_cursor(x=x, y=y, coord_mode=coord_mode, duration=duration)
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def click(
        x: float | None = None,
        y: float | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        coord_mode: CoordMode = "normalized",
    ) -> list[TextContent]:
        """Move the virtual cursor to (x, y) and click, displaying a visual ripple effect.

        Args:
            x: Target X coordinate (optional; clicks current position if omitted).
            y: Target Y coordinate (optional; clicks current position if omitted).
            button: 'left', 'right', or 'middle'.
            clicks: 1 for single click, 2 for double click.
            coord_mode: 'normalized' (0-1000), 'image_pixel', or 'pixel'.
        """
        rt.start()
        try:
            res = rt.controller.click(x=x, y=y, button=button, clicks=clicks, coord_mode=coord_mode)
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def drag(
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        button: Literal["left", "right", "middle"] = "left",
        duration: float = 0.5,
        coord_mode: CoordMode = "normalized",
    ) -> list[TextContent]:
        """Drag from (start_x, start_y) to (end_x, end_y) with visual cursor tracking."""
        rt.start()
        try:
            res = rt.controller.drag(
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                button=button,
                duration=duration,
                coord_mode=coord_mode,
            )
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def scroll(
        x: float | None = None,
        y: float | None = None,
        dx: int = 0,
        dy: int = -3,
        coord_mode: CoordMode = "normalized",
    ) -> list[TextContent]:
        """Scroll horizontally/vertically at optional (x, y). Negative dy scrolls down."""
        rt.start()
        try:
            res = rt.controller.scroll(x=x, y=y, dx=dx, dy=dy, coord_mode=coord_mode)
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def type_text(
        text: str,
        interval: float = 0.02,
        use_clipboard: bool = True,
        press_enter: bool = False,
    ) -> list[TextContent]:
        """Type text into the focused window. Uses clipboard paste by default for Japanese/Unicode support."""
        rt.start()
        try:
            res = rt.controller.type_text(
                text=text,
                interval=interval,
                use_clipboard=use_clipboard,
                press_enter=press_enter,
            )
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def press_hotkey(keys: list[str] | str) -> list[TextContent]:
        """Press a key or hotkey combination (e.g. ['ctrl', 'c'], ['command', 'space'], or 'ctrl+s')."""
        rt.start()
        try:
            res = rt.controller.press_hotkey(keys=keys)
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def focus_app(app_name: str, launch_if_not_running: bool = True) -> list[TextContent]:
        """Bring an allowed application to focus (or launch it). Enforces always_allowed_apps guardrail."""
        rt.start()
        try:
            res = rt.controller.focus_app(app_name=app_name, launch_if_not_running=launch_if_not_running)
            return [_json_text({"ok": True, **res})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def wait(seconds: float = 1.0) -> list[TextContent]:
        """Wait for the specified seconds while respecting emergency stop / human-priority lock."""
        rt.start()
        try:
            end_t = time.monotonic() + max(0.0, min(30.0, float(seconds)))
            while time.monotonic() < end_t:
                rt.safety.ensure_can_act()
                time.sleep(min(0.05, max(0.0, end_t - time.monotonic())))
            return [_json_text({"ok": True, "waited_seconds": seconds})]
        except Exception as exc:
            return _error_payload(exc)

    @mcp.tool()
    def get_screen_info() -> list[TextContent]:
        """Get display geometry, active region, active application, and OS permission status."""
        rt.start()
        info = rt.controller.get_screen_info()
        info["permissions"] = check_os_permissions()
        info["overlay"] = rt.overlay.get_state() if rt.config.overlay.enabled else {"enabled": False}
        return [_json_text({"ok": True, **info})]

    @mcp.tool()
    def get_safety_status() -> list[TextContent]:
        """Return current safety guardrail status, allowlist policy, and kill-switch / pause state."""
        return [_json_text({"ok": True, **rt.safety.get_status()})]

    @mcp.tool()
    def resume_automation() -> list[TextContent]:
        """Resume AI automation after a kill-switch or human-priority pause."""
        status = rt.safety.resume()
        if rt.config.overlay.enabled:
            rt.overlay.set_status("idle")
        return [_json_text({"ok": True, "resumed": True, **status})]

    return mcp, rt


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Computer Use MCP Server")
    parser.add_argument("--config", type=str, default=None, help="Path to config.json or config.toml")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=None,
        help="MCP transport protocol",
    )
    parser.add_argument("--host", type=str, default=None, help="Bind host for HTTP/SSE transports")
    parser.add_argument("--port", type=int, default=None, help="Bind port for HTTP/SSE transports")
    parser.add_argument("--no-overlay", action="store_true", help="Disable virtual cursor visual overlay")
    parser.add_argument("--no-listeners", action="store_true", help="Disable pynput global input listeners")
    parser.add_argument("--dry-run", action="store_true", help="Do not inject real OS mouse/keyboard events")
    args = parser.parse_args()

    runtime = AppRuntime(
        config_path=args.config,
        enable_overlay=not args.no_overlay,
        enable_listeners=not args.no_listeners,
        dry_run=args.dry_run,
    )
    atexit.register(runtime.stop)

    mcp, rt = create_mcp_server(runtime)
    transport = args.transport or rt.config.server.transport
    run_kwargs: dict[str, Any] = {}
    if transport in ("sse", "streamable-http"):
        if args.host:
            mcp.settings.host = args.host  # type: ignore[attr-defined]
        elif rt.config.server.host:
            mcp.settings.host = rt.config.server.host  # type: ignore[attr-defined]
        if args.port:
            mcp.settings.port = args.port  # type: ignore[attr-defined]
        elif rt.config.server.port:
            mcp.settings.port = rt.config.server.port  # type: ignore[attr-defined]

    mcp.run(transport=transport, **run_kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
