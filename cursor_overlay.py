"""Virtual Cursor Visual Overlay for Antigravity Computer Use.

Displays the custom AI pointer image (`assets/virtual_cursor.png`) inside a
transparent, always-on-top, click-through overlay window with smooth eased
movement and visual click ripple/scale animations.

Supports:
- PySide6 backend (primary: macOS, Windows, and X11/Wayland with Qt plugins)
- Tkinter backend (automatic fallback when Qt display plugins are unavailable, e.g. Xvfb)
- Headless tracker backend (for displayless CI environments)

Can be run either:
1. As a standalone IPC subprocess (`python cursor_overlay.py --serve`) controlled
   via JSON-lines over stdin/stdout (required on macOS where AppKit/Qt must own
   the main thread while FastMCP/MCPServer owns the server's main thread).
2. Directly in-process via `CursorOverlayClient` or `PySide6OverlayWindow`.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from config_loader import AppConfig, OverlayConfig, PROJECT_ROOT


def ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


@dataclass
class RippleEffect:
    started_at: float
    duration_sec: float
    max_radius: float
    color_hex: str
    double_wave: bool = False


@dataclass
class OverlayState:
    x: float = 100.0
    y: float = 100.0
    visible: bool = True
    status: str = "idle"  # idle | acting | paused | dragging
    scale: float = 1.0
    ripples: list[RippleEffect] = field(default_factory=list)


def _apply_windows_click_through(hwnd: int, exclude_from_capture: bool = True) -> None:
    """Force WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE on Windows."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOPMOST = 0x00000008
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )
        if exclude_from_capture:
            # WDA_EXCLUDEFROMCAPTURE = 0x00000011 -> visible to humans, hidden from screen capture
            user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
    except Exception:
        pass


def _apply_macos_click_through(qt_or_nsview_ptr: int, exclude_from_capture: bool = True) -> None:
    """Ensure NSWindow ignores mouse events and floats above normal windows on macOS."""
    if sys.platform != "darwin":
        return
    try:
        import objc  # type: ignore
        from AppKit import NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces  # type: ignore

        view = objc.objc_object(c_void_p=qt_or_nsview_ptr)
        ns_win = view.window() if hasattr(view, "window") else None
        if ns_win is not None:
            ns_win.setIgnoresMouseEvents_(True)
            ns_win.setLevel_(NSFloatingWindowLevel + 2)
            ns_win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
            if exclude_from_capture and hasattr(ns_win, "setSharingType_"):
                # NSWindowSharingNone = 0 -> stays always visible on monitor, excluded from capture
                ns_win.setSharingType_(0)
    except Exception:
        pass


def _apply_x11_click_through(win_id: int) -> None:
    """Make X11 window input-transparent using XFixes shape if python-xlib is present."""
    if not sys.platform.startswith("linux") or not win_id:
        return
    try:
        from Xlib import display as xdisplay  # type: ignore
        from Xlib.ext import shape  # type: ignore

        d = xdisplay.Display()
        win = d.create_resource_object("window", win_id)
        if hasattr(shape, "SO"):
            # Empty input region -> all pointer events pass straight through
            win.shape_rectangles(shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
            d.sync()
        d.close()
    except Exception:
        pass


class BaseOverlayBackend:
    """Common interface for overlay implementations."""

    backend_name: str = "base"

    def move_to(self, x: float, y: float, duration: float | None = None, wait: bool = False) -> None:
        raise NotImplementedError

    def trigger_click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        raise NotImplementedError

    def set_status(self, status: str) -> None:
        raise NotImplementedError

    def show(self) -> None:
        raise NotImplementedError

    def hide(self) -> None:
        raise NotImplementedError

    def process_events(self) -> None:
        pass

    def close(self) -> None:
        pass

    def get_state(self) -> dict[str, Any]:
        raise NotImplementedError


class HeadlessOverlayBackend(BaseOverlayBackend):
    """Displayless overlay backend for CI or SSH environments; can render frames via PIL."""

    backend_name = "headless"

    def __init__(self, config: OverlayConfig) -> None:
        self.config = config
        self.state = OverlayState()
        self._cursor_path = config.resolve_cursor_path()
        self._base_image: Image.Image | None = None
        if self._cursor_path.exists():
            self._base_image = Image.open(self._cursor_path).convert("RGBA")
        self.event_log: list[dict[str, Any]] = []

    def move_to(self, x: float, y: float, duration: float | None = None, wait: bool = False) -> None:
        dur = self.config.move_duration_sec if duration is None else max(0.0, duration)
        start_x, start_y = self.state.x, self.state.y
        if self.config.smooth_move and dur > 0 and wait:
            steps = max(2, min(12, int(dur * 30)))
            for i in range(1, steps + 1):
                t = ease_in_out_cubic(i / steps)
                self.state.x = start_x + (x - start_x) * t
                self.state.y = start_y + (y - start_y) * t
                time.sleep(dur / steps)
        self.state.x = float(x)
        self.state.y = float(y)
        self.event_log.append({"type": "move", "x": self.state.x, "y": self.state.y, "t": time.time()})

    def trigger_click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        if x is not None and y is not None:
            self.state.x = float(x)
            self.state.y = float(y)
        ce = self.config.click_effect
        color = ce.double_color if clicks >= 2 else (ce.right_color if button == "right" else ce.left_color)
        self.state.ripples.append(
            RippleEffect(
                started_at=time.monotonic(),
                duration_sec=ce.ripple_duration_ms / 1000.0,
                max_radius=ce.ripple_max_radius,
                color_hex=color,
                double_wave=clicks >= 2,
            )
        )
        self.state.scale = ce.scale_pulse
        self.event_log.append(
            {"type": "click", "x": self.state.x, "y": self.state.y, "button": button, "clicks": clicks, "t": time.time()}
        )

    def set_status(self, status: str) -> None:
        self.state.status = status

    def show(self) -> None:
        self.state.visible = True

    def hide(self) -> None:
        self.state.visible = False

    def render_preview_frame(self, width: int = 800, height: int = 600) -> Image.Image:
        """Render a synthetic preview frame showing the cursor & active ripples."""
        canvas = Image.new("RGBA", (width, height), (24, 28, 36, 255))
        draw = ImageDraw.Draw(canvas, "RGBA")
        cx, cy = int(round(self.state.x)), int(round(self.state.y))
        now = time.monotonic()
        for r in self.state.ripples:
            progress = min(1.0, max(0.0, (now - r.started_at) / max(0.05, r.duration_sec)))
            rad = int(6 + r.max_radius * ease_in_out_cubic(progress))
            alpha = int(220 * (1.0 - progress))
            rgb = tuple(int(r.color_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            draw.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=(*rgb, alpha), width=3)
        if self._base_image is not None:
            size = int(round(self.config.cursor_size * self.state.scale))
            cursor = self._base_image.resize((size, size), Image.Resampling.LANCZOS)
            hx = int(round(size * self.config.hotspot_ratio_x))
            hy = int(round(size * self.config.hotspot_ratio_y))
            canvas.alpha_composite(cursor, (cx - hx, cy - hy))
        return canvas

    def get_state(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "x": self.state.x,
            "y": self.state.y,
            "visible": self.state.visible,
            "status": self.state.status,
            "ripples": len(self.state.ripples),
        }


def _create_pyside6_backend(config: OverlayConfig) -> BaseOverlayBackend:
    """Create a PySide6 transparent click-through overlay backend."""
    from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv[:1])
        owns_app = True

    class _PySide6CursorWidget(QWidget):
        def __init__(self, cfg: OverlayConfig) -> None:
            super().__init__(None)
            self.cfg = cfg
            self.state = OverlayState()
            self._anim_active = False
            self._anim_start_t = 0.0
            self._anim_duration = 0.0
            self._anim_from = (self.state.x, self.state.y)
            self._anim_to = (self.state.x, self.state.y)
            self._pulse_start = 0.0
            self._pulse_active = False

            ce = cfg.click_effect
            self._canvas_size = max(cfg.cursor_size + 32, ce.ripple_max_radius * 2 + cfg.cursor_size + 24)
            self._center = self._canvas_size / 2.0

            cursor_path = cfg.resolve_cursor_path()
            self._pixmap = QPixmap(str(cursor_path)) if cursor_path.exists() else QPixmap()
            if not self._pixmap.isNull():
                self._pixmap = self._pixmap.scaled(
                    cfg.cursor_size,
                    cfg.cursor_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

            flags = (
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus
                | Qt.WindowType.WindowTransparentForInput
            )
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setFixedSize(self._canvas_size, self._canvas_size)
            self.setWindowOpacity(cfg.opacity)

            self._timer = QTimer(self)
            interval_ms = max(8, int(1000 / max(15, cfg.fps)))
            self._timer.setInterval(interval_ms)
            self._timer.timeout.connect(self._on_tick)
            self._timer.start()

            self._reposition_window()
            if self.state.visible:
                self.show()
                self._apply_native_passthrough()

        def _apply_native_passthrough(self) -> None:
            try:
                wid = int(self.winId())
                excl = bool(self.cfg.exclude_from_capture)
                if sys.platform == "win32":
                    _apply_windows_click_through(wid, exclude_from_capture=excl)
                elif sys.platform == "darwin":
                    _apply_macos_click_through(wid, exclude_from_capture=excl)
                else:
                    _apply_x11_click_through(wid)
            except Exception:
                pass

        def _reposition_window(self) -> None:
            # Place canvas center at (state.x, state.y) so ripples center cleanly on hotspot
            win_x = int(round(self.state.x - self._center))
            win_y = int(round(self.state.y - self._center))
            self.move(win_x, win_y)

        def start_move(self, x: float, y: float, duration: float | None = None) -> None:
            dur = self.cfg.move_duration_sec if duration is None else max(0.0, duration)
            if not self.cfg.smooth_move or dur <= 0.01:
                self._anim_active = False
                self.state.x = float(x)
                self.state.y = float(y)
                self._reposition_window()
                self.update()
                return
            self._anim_from = (self.state.x, self.state.y)
            self._anim_to = (float(x), float(y))
            self._anim_start_t = time.monotonic()
            self._anim_duration = dur
            self._anim_active = True

        def trigger_click(self, x: float | None, y: float | None, button: str, clicks: int) -> None:
            if x is not None and y is not None:
                self._anim_active = False
                self.state.x = float(x)
                self.state.y = float(y)
                self._reposition_window()
            ce = self.cfg.click_effect
            if ce.enabled:
                color = ce.double_color if clicks >= 2 else (ce.right_color if button == "right" else ce.left_color)
                self.state.ripples.append(
                    RippleEffect(
                        started_at=time.monotonic(),
                        duration_sec=ce.ripple_duration_ms / 1000.0,
                        max_radius=ce.ripple_max_radius,
                        color_hex=color,
                        double_wave=clicks >= 2,
                    )
                )
                self._pulse_start = time.monotonic()
                self._pulse_active = True
            self.update()

        def _on_tick(self) -> None:
            now = time.monotonic()
            dirty = False

            if self.state.status == "thinking":
                dirty = True

            if self._anim_active:
                elapsed = now - self._anim_start_t
                t = min(1.0, elapsed / max(0.001, self._anim_duration))
                eased = ease_in_out_cubic(t)
                sx, sy = self._anim_from
                tx, ty = self._anim_to
                self.state.x = sx + (tx - sx) * eased
                self.state.y = sy + (ty - sy) * eased
                self._reposition_window()
                if t >= 1.0:
                    self._anim_active = False
                dirty = True

            if self._pulse_active:
                pe = now - self._pulse_start
                pulse_dur = 0.22
                if pe >= pulse_dur:
                    self.state.scale = 1.0
                    self._pulse_active = False
                else:
                    # Shrink quickly then spring back
                    half = pulse_dur * 0.4
                    min_s = self.cfg.click_effect.scale_pulse
                    if pe < half:
                        self.state.scale = 1.0 - (1.0 - min_s) * (pe / half)
                    else:
                        self.state.scale = min_s + (1.0 - min_s) * ((pe - half) / (pulse_dur - half))
                dirty = True

            if self.state.ripples:
                self.state.ripples = [r for r in self.state.ripples if (now - r.started_at) < r.duration_sec]
                dirty = True

            if dirty:
                self.update()

        def paintEvent(self, event: Any) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            cx, cy = self._center, self._center
            now = time.monotonic()

            # 0. Draw spinning "thinking" indicator (ぐるぐるスピナー) when VLM is reasoning
            if self.state.status == "thinking":
                angle_deg = (now * 420.0) % 360.0
                r_outer = max(20.0, self.cfg.cursor_size * 0.56)
                r_inner = max(14.0, r_outer - 6.0)

                # Subtle translucent halo ring
                halo = QColor(self.cfg.thinking_spinner_color)
                halo.setAlpha(45)
                painter.setPen(QPen(halo, 3.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), r_outer, r_outer)

                # Primary spinning arc
                c_main = QColor(self.cfg.thinking_spinner_color)
                c_main.setAlpha(235)
                pen_main = QPen(c_main, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(pen_main)
                outer_rect = QRectF(cx - r_outer, cy - r_outer, r_outer * 2.0, r_outer * 2.0)
                painter.drawArc(outer_rect, int(-angle_deg * 16), int(-245 * 16))

                # Counter-rotating inner accent arc
                c_acc = QColor(self.cfg.thinking_spinner_accent)
                c_acc.setAlpha(215)
                pen_acc = QPen(c_acc, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(pen_acc)
                inner_rect = QRectF(cx - r_inner, cy - r_inner, r_inner * 2.0, r_inner * 2.0)
                painter.drawArc(inner_rect, int(angle_deg * 1.35 * 16), int(140 * 16))

            # 1. Draw click ripple animations centered on the cursor hotspot
            for r in self.state.ripples:
                prog = min(1.0, max(0.0, (now - r.started_at) / max(0.05, r.duration_sec)))
                eased = 1.0 - (1.0 - prog) ** 3
                radius = 5.0 + r.max_radius * eased
                alpha = int(230 * (1.0 - prog))
                color = QColor(r.color_hex)
                color.setAlpha(alpha)
                pen = QPen(color, max(1.5, 3.5 * (1.0 - prog)))
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), radius, radius)

                fill = QColor(r.color_hex)
                fill.setAlpha(int(55 * (1.0 - prog)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawEllipse(QPointF(cx, cy), radius * 0.65, radius * 0.65)

                if r.double_wave and prog > 0.18:
                    prog2 = min(1.0, (prog - 0.18) / 0.82)
                    rad2 = 4.0 + r.max_radius * 0.78 * (1.0 - (1.0 - prog2) ** 3)
                    c2 = QColor(r.color_hex)
                    c2.setAlpha(int(200 * (1.0 - prog2)))
                    painter.setPen(QPen(c2, 2.2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(cx, cy), rad2, rad2)

            # 2. Draw virtual cursor image anchored so its hotspot lands at (cx, cy)
            pulse_breath = 1.0 + 0.05 * math.sin(now * 6.5) if self.state.status == "thinking" else 1.0
            size = self.cfg.cursor_size * self.state.scale * pulse_breath
            hx = size * self.cfg.hotspot_ratio_x
            hy = size * self.cfg.hotspot_ratio_y
            draw_x = cx - hx
            draw_y = cy - hy

            if not self._pixmap.isNull():
                target_rect = QRectF(draw_x, draw_y, size, size)
                source_rect = QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
                painter.drawPixmap(target_rect, self._pixmap, source_rect)
            else:
                # Vector fallback if asset is missing
                path = QPainterPath()
                path.moveTo(cx, cy)
                path.lineTo(cx - 22, cy + 8)
                path.lineTo(cx - 10, cy + 12)
                path.lineTo(cx - 6, cy + 24)
                path.closeSubpath()
                painter.setBrush(QColor("#FFFFFF"))
                painter.setPen(QPen(QColor("#111111"), 2.5))
                painter.drawPath(path)

            # 3. Optional compact status pill ("AI" / "思考中" / "PAUSED")
            if self.cfg.show_label:
                if self.state.status == "paused":
                    badge_text, bg_hex, bd_hex, bw = "PAUSED", "#EF4444", "#FCA5A5", 46
                elif self.state.status == "thinking":
                    badge_text, bg_hex, bd_hex, bw = "思考中", "#1E1B4B", "#A855F7", 42
                else:
                    badge_text, bg_hex, bd_hex, bw = self.cfg.label_text, "#0F172A", "#38BDF8", 24
                badge_bg = QColor(bg_hex)
                badge_bg.setAlpha(215)
                badge_border = QColor(bd_hex)
                badge_border.setAlpha(225)

                bx = draw_x + size * 0.08
                by = draw_y + size * 0.78
                bh = 15
                painter.setPen(QPen(badge_border, 1.0))
                painter.setBrush(badge_bg)
                painter.drawRoundedRect(QRectF(bx, by, bw, bh), 5.0, 5.0)
                painter.setPen(QColor("#F8FAFC"))
                font = QFont("Sans", 8, QFont.Weight.Bold)
                painter.setFont(font)
                painter.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, badge_text)

            painter.end()

    class _PySide6Backend(BaseOverlayBackend):
        backend_name = "pyside6"

        def __init__(self, cfg: OverlayConfig) -> None:
            self.cfg = cfg
            self.app = app
            self._owns_app = owns_app
            self.widget = _PySide6CursorWidget(cfg)

        def move_to(self, x: float, y: float, duration: float | None = None, wait: bool = False) -> None:
            self.widget.start_move(x, y, duration)
            if wait:
                dur = self.cfg.move_duration_sec if duration is None else max(0.0, duration)
                deadline = time.monotonic() + (dur if self.cfg.smooth_move else 0.0) + 0.02
                while time.monotonic() < deadline and self.widget._anim_active:
                    self.app.processEvents()
                    time.sleep(0.01)
            self.app.processEvents()

        def trigger_click(
            self,
            x: float | None = None,
            y: float | None = None,
            button: str = "left",
            clicks: int = 1,
        ) -> None:
            self.widget.trigger_click(x, y, button, clicks)
            self.app.processEvents()

        def set_status(self, status: str) -> None:
            self.widget.state.status = status
            self.widget.update()
            self.app.processEvents()

        def show(self) -> None:
            self.widget.state.visible = True
            self.widget.show()
            self.widget._apply_native_passthrough()
            self.app.processEvents()

        def hide(self) -> None:
            self.widget.state.visible = False
            self.widget.hide()
            self.app.processEvents()

        def process_events(self) -> None:
            self.app.processEvents()

        def close(self) -> None:
            self.widget.close()
            self.app.processEvents()

        def get_state(self) -> dict[str, Any]:
            return {
                "backend": self.backend_name,
                "x": self.widget.state.x,
                "y": self.widget.state.y,
                "visible": self.widget.state.visible,
                "status": self.widget.state.status,
                "ripples": len(self.widget.state.ripples),
            }

    return _PySide6Backend(config)


def _create_tkinter_backend(config: OverlayConfig) -> BaseOverlayBackend:
    """Create a Tkinter overlay backend (fallback for Xvfb / minimal X11)."""
    import tkinter as tk
    from PIL import ImageTk

    class _TkinterBackend(BaseOverlayBackend):
        backend_name = "tkinter"

        def __init__(self, cfg: OverlayConfig) -> None:
            self.cfg = cfg
            self.state = OverlayState()
            self.root = tk.Tk()
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self._transparent_key = "#010203"
            if sys.platform == "win32":
                self.root.attributes("-transparentcolor", self._transparent_key)
                self.root.configure(bg=self._transparent_key)
            elif sys.platform == "darwin":
                self.root.attributes("-transparent", True)
                self.root.configure(bg="systemTransparent")
            else:
                self.root.configure(bg=self._transparent_key)

            ce = cfg.click_effect
            self._canvas_size = max(cfg.cursor_size + 24, ce.ripple_max_radius * 2 + cfg.cursor_size + 16)
            self._center = self._canvas_size // 2
            self.canvas = tk.Canvas(
                self.root,
                width=self._canvas_size,
                height=self._canvas_size,
                bg=self._transparent_key,
                highlightthickness=0,
                bd=0,
            )
            self.canvas.pack()

            cursor_path = cfg.resolve_cursor_path()
            self._base_img: Image.Image | None = None
            if cursor_path.exists():
                self._base_img = Image.open(cursor_path).convert("RGBA")
            self._tk_photo: Any = None
            self._redraw()
            self._reposition()
            self.root.update_idletasks()
            if sys.platform == "win32":
                _apply_windows_click_through(self.root.winfo_id())
            elif sys.platform.startswith("linux"):
                _apply_x11_click_through(self.root.winfo_id())
            self.root.update()

        def _reposition(self) -> None:
            wx = int(round(self.state.x - self._center))
            wy = int(round(self.state.y - self._center))
            self.root.geometry(f"{self._canvas_size}x{self._canvas_size}+{wx}+{wy}")

        def _redraw(self) -> None:
            self.canvas.delete("all")
            cx, cy = self._center, self._center
            now = time.monotonic()
            self.state.ripples = [r for r in self.state.ripples if (now - r.started_at) < r.duration_sec]
            for r in self.state.ripples:
                prog = min(1.0, max(0.0, (now - r.started_at) / max(0.05, r.duration_sec)))
                rad = 5.0 + r.max_radius * (1.0 - (1.0 - prog) ** 3)
                self.canvas.create_oval(
                    cx - rad, cy - rad, cx + rad, cy + rad, outline=r.color_hex, width=max(1, int(3 * (1.0 - prog)))
                )
            if self._base_img is not None:
                size = max(12, int(round(self.cfg.cursor_size * self.state.scale)))
                resized = self._base_img.resize((size, size), Image.Resampling.LANCZOS)
                self._tk_photo = ImageTk.PhotoImage(resized)
                hx = int(round(size * self.cfg.hotspot_ratio_x))
                hy = int(round(size * self.cfg.hotspot_ratio_y))
                self.canvas.create_image(cx - hx, cy - hy, anchor="nw", image=self._tk_photo)
            if self.cfg.show_label:
                label = "PAUSED" if self.state.status == "paused" else self.cfg.label_text
                self.canvas.create_text(
                    cx - 10,
                    cy + 18,
                    text=label,
                    fill="#38BDF8" if self.state.status != "paused" else "#EF4444",
                    font=("Sans", 8, "bold"),
                )

        def move_to(self, x: float, y: float, duration: float | None = None, wait: bool = False) -> None:
            dur = self.cfg.move_duration_sec if duration is None else max(0.0, duration)
            sx, sy = self.state.x, self.state.y
            if self.cfg.smooth_move and dur > 0.01:
                steps = max(3, int(dur * min(30, self.cfg.fps)))
                for i in range(1, steps + 1):
                    t = ease_in_out_cubic(i / steps)
                    self.state.x = sx + (x - sx) * t
                    self.state.y = sy + (y - sy) * t
                    self._reposition()
                    self._redraw()
                    self.root.update()
                    time.sleep(dur / steps)
            self.state.x = float(x)
            self.state.y = float(y)
            self._reposition()
            self._redraw()
            self.root.update()

        def trigger_click(
            self,
            x: float | None = None,
            y: float | None = None,
            button: str = "left",
            clicks: int = 1,
        ) -> None:
            if x is not None and y is not None:
                self.state.x = float(x)
                self.state.y = float(y)
                self._reposition()
            ce = self.cfg.click_effect
            if ce.enabled:
                color = ce.double_color if clicks >= 2 else (ce.right_color if button == "right" else ce.left_color)
                self.state.ripples.append(
                    RippleEffect(
                        started_at=time.monotonic(),
                        duration_sec=ce.ripple_duration_ms / 1000.0,
                        max_radius=ce.ripple_max_radius,
                        color_hex=color,
                        double_wave=clicks >= 2,
                    )
                )
                self.state.scale = ce.scale_pulse
                self._redraw()
                self.root.update()
                time.sleep(0.07)
                self.state.scale = 1.0
                self._redraw()
                self.root.update()

        def set_status(self, status: str) -> None:
            self.state.status = status
            self._redraw()
            self.root.update()

        def show(self) -> None:
            self.state.visible = True
            self.root.deiconify()
            self.root.update()

        def hide(self) -> None:
            self.state.visible = False
            self.root.withdraw()
            self.root.update()

        def process_events(self) -> None:
            if self.state.ripples:
                self._redraw()
            self.root.update()

        def close(self) -> None:
            try:
                self.root.destroy()
            except Exception:
                pass

        def get_state(self) -> dict[str, Any]:
            return {
                "backend": self.backend_name,
                "x": self.state.x,
                "y": self.state.y,
                "visible": self.state.visible,
                "status": self.state.status,
                "ripples": len(self.state.ripples),
            }

    return _TkinterBackend(config)


def create_overlay_backend(config: OverlayConfig, backend: str = "auto") -> BaseOverlayBackend:
    """Instantiate the best available overlay backend."""
    if backend == "headless":
        return HeadlessOverlayBackend(config)

    if backend in ("auto", "pyside6"):
        # Probe PySide6 in a non-crashing way when auto-selecting on Linux without xcb-cursor0
        if backend == "pyside6":
            return _create_pyside6_backend(config)
        if sys.platform.startswith("linux") and not os.environ.get("QT_QPA_PLATFORM"):
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from PySide6.QtWidgets import QApplication; app = QApplication([]); app.quit()",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if probe.returncode == 0:
                return _create_pyside6_backend(config)
        else:
            try:
                return _create_pyside6_backend(config)
            except Exception:
                pass

    if backend in ("auto", "tkinter"):
        try:
            return _create_tkinter_backend(config)
        except Exception:
            if backend == "tkinter":
                raise

    return HeadlessOverlayBackend(config)


class CursorOverlayClient:
    """Thread-safe controller that manages `cursor_overlay.py --serve` subprocess via JSON IPC."""

    def __init__(self, config: OverlayConfig, backend: str = "auto") -> None:
        self.config = config
        self.backend_preference = backend
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._started = False
        self._active_backend = "none"

    def start(self) -> dict[str, Any]:
        with self._lock:
            if not self.config.enabled:
                return {"ok": True, "enabled": False, "backend": "disabled"}
            if self._proc is not None and self._proc.poll() is None:
                return {"ok": True, "enabled": True, "backend": self._active_backend}

            if getattr(sys, "frozen", False):
                cmd = [
                    sys.executable,
                    "--cursor-overlay-serve",
                    "--backend",
                    self.backend_preference,
                ]
            else:
                cmd = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--serve",
                    "--backend",
                    self.backend_preference,
                ]
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            # Wait briefly for ready handshake
            ready_line = ""
            if self._proc.stdout is not None:
                ready_line = self._proc.stdout.readline().strip()
            if ready_line:
                try:
                    info = json.loads(ready_line)
                    self._active_backend = str(info.get("backend", "unknown"))
                    self._started = True
                    return {"ok": True, "enabled": True, "backend": self._active_backend}
                except json.JSONDecodeError:
                    pass
            return {"ok": False, "enabled": True, "backend": "failed", "detail": ready_line}

    def _send(self, payload: dict[str, Any], expect_reply: bool = True) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": True, "disabled": True}
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                # Lazy start
                pass
            if self._proc is None or self._proc.poll() is not None:
                self._lock.release()
                try:
                    res = self.start()
                    if not res.get("ok"):
                        return res
                finally:
                    self._lock.acquire()

            assert self._proc is not None and self._proc.stdin is not None
            try:
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
                if expect_reply and self._proc.stdout is not None:
                    line = self._proc.stdout.readline().strip()
                    if line:
                        return json.loads(line)
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    def move_to(self, x: float, y: float, duration: float | None = None, wait: bool = True) -> dict[str, Any]:
        return self._send({"cmd": "move", "x": x, "y": y, "duration": duration, "wait": wait})

    def trigger_click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        return self._send({"cmd": "click", "x": x, "y": y, "button": button, "clicks": clicks})

    def set_status(self, status: str) -> dict[str, Any]:
        return self._send({"cmd": "status", "status": status})

    def show(self) -> dict[str, Any]:
        return self._send({"cmd": "show"})

    def hide(self) -> dict[str, Any]:
        return self._send({"cmd": "hide"})

    def get_state(self) -> dict[str, Any]:
        return self._send({"cmd": "state"})

    def close(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    assert self._proc.stdin is not None
                    self._proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=2.0)
                except Exception:
                    self._proc.terminate()
            self._proc = None


def _serve_ipc(config_path: str | None = None, backend_name: str = "auto") -> int:
    """Run overlay GUI event loop on the main thread and read JSON-lines commands from stdin."""
    cfg = AppConfig.load(config_path).overlay
    overlay = create_overlay_backend(cfg, backend=backend_name)

    cmd_queue: list[dict[str, Any]] = []
    q_lock = threading.Lock()
    stop_flag = threading.Event()

    def _stdin_reader() -> None:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with q_lock:
                cmd_queue.append(msg)
            if msg.get("cmd") == "quit":
                stop_flag.set()
                break
        stop_flag.set()

    reader = threading.Thread(target=_stdin_reader, daemon=True)
    reader.start()

    # Emit ready handshake
    sys.stdout.write(json.dumps({"event": "ready", "backend": overlay.backend_name}) + "\n")
    sys.stdout.flush()

    try:
        while not stop_flag.is_set():
            pending: list[dict[str, Any]] = []
            with q_lock:
                if cmd_queue:
                    pending = cmd_queue[:]
                    cmd_queue.clear()

            for msg in pending:
                cmd = msg.get("cmd")
                reply: dict[str, Any] = {"ok": True}
                if cmd == "move":
                    overlay.move_to(
                        float(msg["x"]),
                        float(msg["y"]),
                        duration=msg.get("duration"),
                        wait=bool(msg.get("wait", True)),
                    )
                    reply["state"] = overlay.get_state()
                elif cmd == "click":
                    overlay.trigger_click(
                        x=msg.get("x"),
                        y=msg.get("y"),
                        button=str(msg.get("button", "left")),
                        clicks=int(msg.get("clicks", 1)),
                    )
                    reply["state"] = overlay.get_state()
                elif cmd == "status":
                    overlay.set_status(str(msg.get("status", "idle")))
                    reply["state"] = overlay.get_state()
                elif cmd == "show":
                    overlay.show()
                elif cmd == "hide":
                    overlay.hide()
                elif cmd == "state" or cmd == "ping":
                    reply["state"] = overlay.get_state()
                elif cmd == "quit":
                    sys.stdout.write(json.dumps({"ok": True, "quit": True}) + "\n")
                    sys.stdout.flush()
                    overlay.close()
                    return 0

                sys.stdout.write(json.dumps(reply) + "\n")
                sys.stdout.flush()

            overlay.process_events()
            time.sleep(0.012)
    finally:
        overlay.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Virtual Cursor Overlay")
    parser.add_argument("--serve", action="store_true", help="Run JSON-lines IPC overlay server")
    parser.add_argument("--config", type=str, default=None, help="Path to config.json")
    parser.add_argument(
        "--backend",
        choices=["auto", "pyside6", "tkinter", "headless"],
        default="auto",
        help="Overlay rendering backend",
    )
    parser.add_argument("--demo", action="store_true", help="Run a short visual movement, thinking spinner & click demo")
    parser.add_argument("--always-on", action="store_true", help="Keep the virtual cursor visibly pinned on screen")
    args = parser.parse_args()

    if args.serve:
        return _serve_ipc(config_path=args.config, backend_name=args.backend)

    if args.always_on:
        cfg = AppConfig.load(args.config).overlay
        overlay = create_overlay_backend(cfg, backend=args.backend)
        try:
            while True:
                overlay.process_events()
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass
        finally:
            overlay.close()
        return 0

    if args.demo:
        cfg = AppConfig.load(args.config).overlay
        overlay = create_overlay_backend(cfg, backend=args.backend)
        try:
            overlay.move_to(360, 260, duration=0.35, wait=True)
            overlay.set_status("thinking")
            end_think = time.monotonic() + 1.2
            while time.monotonic() < end_think:
                overlay.process_events()
                time.sleep(0.016)
            overlay.set_status("acting")

            for (tx, ty, btn, clks) in [
                (300, 250, "left", 1),
                (620, 420, "left", 2),
                (450, 340, "right", 1),
            ]:
                overlay.move_to(tx, ty, duration=0.4, wait=True)
                overlay.trigger_click(tx, ty, button=btn, clicks=clks)
                end_t = time.monotonic() + 0.55
                while time.monotonic() < end_t:
                    overlay.process_events()
                    time.sleep(0.016)
        finally:
            overlay.close()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
