"""Cross-platform GUI automation, screen capture, coordinate conversion, and clipboard input."""

from __future__ import annotations

import base64
import ctypes
import io
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal

import mss
from PIL import Image

from config_loader import AppConfig
from cursor_overlay import CursorOverlayClient, ease_in_out_cubic
from safety import SafetyAbortError, SafetyMonitor, get_active_app_name

CoordMode = Literal["normalized", "pixel", "image_pixel"]


def _enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_windows_dpi_awareness()


@dataclass
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int
    display_index: int = 1
    target: str = "display"
    image_width: int | None = None
    image_height: int | None = None
    normalized_range: int = 1000

    @property
    def effective_image_width(self) -> int:
        return self.image_width or self.width

    @property
    def effective_image_height(self) -> int:
        return self.image_height or self.height


class CoordinateConverter:
    """Bidirectional converter between normalized (0..1000), image pixel, and desktop pixel coordinates."""

    def __init__(self, region: CaptureRegion) -> None:
        self.region = region

    def to_pixels(self, x: float, y: float, coord_mode: CoordMode = "normalized") -> tuple[int, int]:
        r = self.region
        if coord_mode == "normalized":
            nx = max(0.0, min(float(r.normalized_range), float(x)))
            ny = max(0.0, min(float(r.normalized_range), float(y)))
            px = r.left + int(round((nx / float(r.normalized_range)) * max(1, r.width - 1)))
            py = r.top + int(round((ny / float(r.normalized_range)) * max(1, r.height - 1)))
            return px, py

        if coord_mode == "image_pixel":
            sx = r.width / max(1, r.effective_image_width)
            sy = r.height / max(1, r.effective_image_height)
            px = r.left + int(round(float(x) * sx))
            py = r.top + int(round(float(y) * sy))
            return px, py

        # "pixel": desktop screen coordinates
        return int(round(float(x))), int(round(float(y)))

    def to_normalized(self, px: float, py: float) -> tuple[float, float]:
        r = self.region
        nx = ((float(px) - r.left) / max(1, r.width - 1)) * float(r.normalized_range)
        ny = ((float(py) - r.top) / max(1, r.height - 1)) * float(r.normalized_range)
        nx = round(max(0.0, min(float(r.normalized_range), nx)), 2)
        ny = round(max(0.0, min(float(r.normalized_range), ny)), 2)
        return nx, ny


def _get_active_window_bounds() -> tuple[int, int, int, int] | None:
    """Return (left, top, width, height) of the active foreground window, or None."""
    if sys.platform == "win32":
        try:
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                rect = RECT()
                if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    w = max(1, rect.right - rect.left)
                    h = max(1, rect.bottom - rect.top)
                    return (rect.left, rect.top, w, h)
        except Exception:
            return None

    if sys.platform == "darwin":
        try:
            from Quartz import (  # type: ignore
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListExcludeDesktopElements,
                kCGWindowListOptionOnScreenOnly,
            )

            windows = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                kCGNullWindowID,
            )
            for w in windows:
                if int(w.get("kCGWindowLayer", 1)) == 0:
                    b = w.get("kCGWindowBounds", {})
                    width = int(b.get("Width", 0))
                    height = int(b.get("Height", 0))
                    if width > 50 and height > 50:
                        return (int(b.get("X", 0)), int(b.get("Y", 0)), width, height)
        except Exception:
            return None

    if sys.platform.startswith("linux"):
        try:
            out = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                text=True,
                timeout=1.5,
                stderr=subprocess.DEVNULL,
            )
            vals: dict[str, int] = {}
            for line in out.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    if v.strip().lstrip("-").isdigit():
                        vals[k.strip()] = int(v.strip())
            if {"X", "Y", "WIDTH", "HEIGHT"}.issubset(vals):
                return (vals["X"], vals["Y"], max(1, vals["WIDTH"]), max(1, vals["HEIGHT"]))
        except Exception:
            return None

    return None


def _normalize_key_name(key: str) -> str:
    k = key.strip().lower()
    aliases = {
        "control": "ctrl",
        "ctrl": "ctrl",
        "lctrl": "ctrlleft",
        "rctrl": "ctrlright",
        "option": "alt",
        "alt": "alt",
        "return": "enter",
        "enter": "enter",
        "escape": "esc",
        "esc": "esc",
        "spacebar": "space",
        "space": "space",
        "del": "delete",
        "backspace": "backspace",
        "pgup": "pageup",
        "pgdn": "pagedown",
        "arrowup": "up",
        "arrowdown": "down",
        "arrowleft": "left",
        "arrowright": "right",
    }
    if k in ("cmd", "command", "meta", "super", "win", "windows"):
        if sys.platform == "darwin":
            return "command"
        if sys.platform == "win32":
            return "win"
        return "super"
    return aliases.get(k, k)


def _clipboard_get() -> str | None:
    try:
        if sys.platform == "darwin":
            return subprocess.check_output(["pbpaste"], text=True, timeout=1.5)
        import pyperclip

        return pyperclip.paste()
    except Exception:
        return None


def _clipboard_set(text: str) -> bool:
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(["pbcopy"], input=text, text=True, timeout=2.0, check=True)
            return proc.returncode == 0
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        if sys.platform.startswith("linux"):
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, input=text, text=True, timeout=2.0, check=True)
                        return True
                    except Exception:
                        pass
        return False


class GUIController:
    """Coordinates screen capture, OS input actions, safety guardrails, and virtual cursor overlay."""

    def __init__(
        self,
        config: AppConfig,
        safety: SafetyMonitor,
        overlay: CursorOverlayClient | None = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.safety = safety
        self.overlay = overlay
        self.dry_run = dry_run
        self._pyautogui: Any = None
        self._last_region = self._init_default_region()

    def _get_pyautogui(self) -> Any:
        if self._pyautogui is None:
            import pyautogui

            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0.0
            self._pyautogui = pyautogui
        return self._pyautogui

    def _init_default_region(self) -> CaptureRegion:
        try:
            with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
                idx = self.config.display.default_display_index
                if 0 <= idx < len(sct.monitors):
                    m = sct.monitors[idx]
                else:
                    m = sct.monitors[0]
                return CaptureRegion(
                    left=int(m["left"]),
                    top=int(m["top"]),
                    width=int(m["width"]),
                    height=int(m["height"]),
                    display_index=idx,
                    normalized_range=self.config.display.normalized_range,
                )
        except Exception:
            return CaptureRegion(
                left=0,
                top=0,
                width=1920,
                height=1080,
                display_index=1,
                normalized_range=self.config.display.normalized_range,
            )

    def get_screen_info(self) -> dict[str, Any]:
        monitors: list[dict[str, Any]] = []
        try:
            with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
                for i, m in enumerate(sct.monitors):
                    monitors.append(
                        {
                            "index": i,
                            "label": "all_displays" if i == 0 else ("primary" if i == 1 else f"display_{i}"),
                            "left": int(m["left"]),
                            "top": int(m["top"]),
                            "width": int(m["width"]),
                            "height": int(m["height"]),
                        }
                    )
        except Exception as exc:
            monitors.append({"index": 1, "error": str(exc), "width": 1920, "height": 1080})

        return {
            "platform": sys.platform,
            "monitors": monitors,
            "active_region": {
                "left": self._last_region.left,
                "top": self._last_region.top,
                "width": self._last_region.width,
                "height": self._last_region.height,
                "display_index": self._last_region.display_index,
                "target": self._last_region.target,
                "normalized_range": self._last_region.normalized_range,
            },
            "active_app": get_active_app_name(),
        }

    def take_screenshot(
        self,
        display_index: int | None = None,
        target: Literal["display", "active_window", "screen_sharing"] = "display",
        image_format: Literal["png", "jpeg"] | None = None,
        max_width: int | None = None,
        include_cursor: bool = False,
    ) -> dict[str, Any]:
        """Capture display, active window, or Mac Screen Sharing window via mss and return Base64 image."""
        idx = self.config.display.default_display_index if display_index is None else display_index
        fmt = (image_format or self.config.display.screenshot_format).lower()
        if fmt not in ("png", "jpeg"):
            fmt = "png"
        mw = self.config.display.max_screenshot_width if max_width is None else max_width

        # Only hide overlay if explicitly configured; otherwise rely on OS exclude_from_capture
        # so the virtual cursor remains visibly pinned on screen without flickering.
        hid_overlay = False
        if (
            not include_cursor
            and self.overlay is not None
            and self.config.overlay.enabled
            and self.config.overlay.hide_during_screenshot
        ):
            self.overlay.hide()
            hid_overlay = True
            time.sleep(0.03)

        try:
            with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
                if target == "screen_sharing":
                    bounds = _get_screen_sharing_window_bounds()
                    if bounds is not None:
                        left, top, width, height = bounds
                        region_dict = {"left": left, "top": top, "width": width, "height": height}
                    else:
                        m = sct.monitors[idx] if 0 <= idx < len(sct.monitors) else sct.monitors[0]
                        region_dict = dict(m)
                        target = "display"
                elif target == "active_window":
                    bounds = _get_active_window_bounds()
                    if bounds is not None:
                        left, top, width, height = bounds
                        region_dict = {"left": left, "top": top, "width": width, "height": height}
                    else:
                        m = sct.monitors[idx] if 0 <= idx < len(sct.monitors) else sct.monitors[0]
                        region_dict = dict(m)
                        target = "display"
                else:
                    m = sct.monitors[idx] if 0 <= idx < len(sct.monitors) else sct.monitors[0]
                    region_dict = dict(m)

                raw = sct.grab(region_dict)
                pil_img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        finally:
            if hid_overlay and self.overlay is not None:
                self.overlay.show()

        orig_w, orig_h = pil_img.size
        if mw and orig_w > mw:
            ratio = mw / float(orig_w)
            new_h = max(1, int(round(orig_h * ratio)))
            pil_img = pil_img.resize((mw, new_h), Image.Resampling.LANCZOS)

        img_w, img_h = pil_img.size
        self._last_region = CaptureRegion(
            left=int(region_dict["left"]),
            top=int(region_dict["top"]),
            width=int(region_dict["width"]),
            height=int(region_dict["height"]),
            display_index=idx,
            target=target,
            image_width=img_w,
            image_height=img_h,
            normalized_range=self.config.display.normalized_range,
        )

        buf = io.BytesIO()
        if fmt == "jpeg":
            pil_img.save(buf, format="JPEG", quality=self.config.display.jpeg_quality, optimize=True)
            mime_type = "image/jpeg"
        else:
            pil_img.save(buf, format="PNG", optimize=True)
            mime_type = "image/png"

        raw_bytes = buf.getvalue()
        b64_data = base64.b64encode(raw_bytes).decode("ascii")

        return {
            "base64": b64_data,
            "bytes": raw_bytes,
            "mime_type": mime_type,
            "format": fmt,
            "metadata": {
                "target": target,
                "display_index": idx,
                "active_app": get_active_app_name(),
                "capture_region": {
                    "left": self._last_region.left,
                    "top": self._last_region.top,
                    "width": self._last_region.width,
                    "height": self._last_region.height,
                },
                "original_size": {"width": orig_w, "height": orig_h},
                "image_size": {"width": img_w, "height": img_h},
                "normalized_range": self.config.display.normalized_range,
            },
        }

    def take_video_frames(
        self,
        frame_count: int = 3,
        interval_sec: float = 0.25,
        display_index: int | None = None,
        target: Literal["display", "active_window", "screen_sharing"] = "display",
        image_format: Literal["png", "jpeg"] = "jpeg",
        max_width: int | None = 1280,
        include_cursor: bool = False,
    ) -> dict[str, Any]:
        """Capture a short sequence of frames for video-capable VLMs like Gemma 4 12B."""
        count = max(1, min(10, int(frame_count)))
        delay = max(0.05, min(2.0, float(interval_sec)))
        frames: list[dict[str, Any]] = []
        t0 = time.monotonic()

        for i in range(count):
            self.safety.ensure_can_act()
            shot = self.take_screenshot(
                display_index=display_index,
                target=target,
                image_format=image_format,
                max_width=max_width,
                include_cursor=include_cursor,
            )
            frames.append(
                {
                    "frame_index": i,
                    "timestamp_offset_sec": round(time.monotonic() - t0, 3),
                    "base64": shot["base64"],
                    "mime_type": shot["mime_type"],
                    "metadata": shot["metadata"],
                }
            )
            if i < count - 1:
                time.sleep(delay)

        return {
            "frame_count": len(frames),
            "interval_sec": delay,
            "target": frames[-1]["metadata"]["target"] if frames else target,
            "frames": frames,
        }

    def connect_screen_sharing(self, host: str | None = None, port: int = 5900) -> dict[str, Any]:
        """Open or focus macOS Screen Sharing (or VNC viewer) for remote Mac / VM control."""
        self.safety.ensure_can_act()
        self.safety.require_app_allowed("Screen Sharing")

        if self.dry_run:
            return {"action": "connect_screen_sharing", "host": host, "port": port, "dry_run": True}

        if sys.platform == "darwin":
            if host and host.strip():
                clean_host = host.strip()
                url = clean_host if clean_host.startswith("vnc://") else f"vnc://{clean_host}:{port}"
                res = subprocess.run(["open", url], capture_output=True, text=True, timeout=5.0)
                ok = res.returncode == 0
                detail = f"Opened {url}" if ok else res.stderr.strip()
            else:
                res = subprocess.run(["open", "-a", "Screen Sharing"], capture_output=True, text=True, timeout=5.0)
                ok = res.returncode == 0
                detail = "Opened Screen Sharing.app" if ok else res.stderr.strip()
            return {
                "action": "connect_screen_sharing",
                "host": host,
                "port": port,
                "opened": ok,
                "detail": detail,
                "screen_sharing_bounds": _get_screen_sharing_window_bounds(),
            }

        return {
            "action": "connect_screen_sharing",
            "opened": False,
            "detail": "Built-in Screen Sharing launcher is macOS-specific; focus your VNC/RDP window via focus_app.",
        }

    def resolve_coordinates(self, x: float, y: float, coord_mode: CoordMode = "normalized") -> tuple[int, int]:
        conv = CoordinateConverter(self._last_region)
        return conv.to_pixels(x, y, coord_mode=coord_mode)

    def _preflight_check(self, check_active_app: bool = True) -> None:
        self.safety.ensure_can_act()
        if check_active_app:
            active_app = get_active_app_name()
            self.safety.require_app_allowed(active_app)

    def _smooth_move_both(self, target_x: int, target_y: int, duration: float) -> None:
        """Move both the virtual cursor overlay and the OS pointer smoothly with safety checks."""
        if self.overlay is not None and self.config.overlay.enabled:
            self.overlay.set_status("acting")
            self.overlay.move_to(target_x, target_y, duration=duration, wait=False)

        if self.dry_run:
            self.safety.update_expected_cursor(target_x, target_y)
            if duration > 0:
                time.sleep(min(duration, 0.05))
            return

        pag = self._get_pyautogui()
        cur_x, cur_y = pag.position()
        if duration <= 0.02:
            self.safety.update_expected_cursor(target_x, target_y)
            pag.moveTo(target_x, target_y)
            return

        steps = max(4, min(30, int(duration * 45)))
        step_sleep = duration / steps
        for i in range(1, steps + 1):
            self.safety.ensure_can_act()
            t = ease_in_out_cubic(i / steps)
            ix = int(round(cur_x + (target_x - cur_x) * t))
            iy = int(round(cur_y + (target_y - cur_y) * t))
            self.safety.update_expected_cursor(ix, iy)
            pag.moveTo(ix, iy)
            time.sleep(step_sleep)

    def move_cursor(
        self,
        x: float,
        y: float,
        coord_mode: CoordMode = "normalized",
        duration: float | None = None,
    ) -> dict[str, Any]:
        self._preflight_check(check_active_app=False)
        px, py = self.resolve_coordinates(x, y, coord_mode=coord_mode)
        dur = self.config.overlay.move_duration_sec if duration is None else max(0.0, float(duration))

        with self.safety.ai_action_guard(px, py):
            self._smooth_move_both(px, py, dur)

        nx, ny = CoordinateConverter(self._last_region).to_normalized(px, py)
        return {
            "action": "move_cursor",
            "pixel": {"x": px, "y": py},
            "normalized": {"x": nx, "y": ny},
            "duration": dur,
        }

    def click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        coord_mode: CoordMode = "normalized",
        move_duration: float | None = None,
    ) -> dict[str, Any]:
        self._preflight_check(check_active_app=True)
        if x is not None and y is not None:
            px, py = self.resolve_coordinates(x, y, coord_mode=coord_mode)
        else:
            if self.dry_run:
                px, py = (0, 0)
            else:
                pos = self._get_pyautogui().position()
                px, py = int(pos.x), int(pos.y)

        dur = self.config.overlay.move_duration_sec if move_duration is None else max(0.0, float(move_duration))
        with self.safety.ai_action_guard(px, py):
            if x is not None and y is not None:
                self._smooth_move_both(px, py, dur)
            if self.overlay is not None and self.config.overlay.enabled:
                self.overlay.trigger_click(px, py, button=button, clicks=clicks)
            if not self.dry_run:
                self._get_pyautogui().click(x=px, y=py, clicks=clicks, interval=0.08, button=button)
            time.sleep(self.config.actions.action_delay_sec)

        nx, ny = CoordinateConverter(self._last_region).to_normalized(px, py)
        return {
            "action": "click",
            "button": button,
            "clicks": clicks,
            "pixel": {"x": px, "y": py},
            "normalized": {"x": nx, "y": ny},
        }

    def drag(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        button: Literal["left", "right", "middle"] = "left",
        duration: float = 0.5,
        coord_mode: CoordMode = "normalized",
    ) -> dict[str, Any]:
        self._preflight_check(check_active_app=True)
        sx, sy = self.resolve_coordinates(start_x, start_y, coord_mode=coord_mode)
        ex, ey = self.resolve_coordinates(end_x, end_y, coord_mode=coord_mode)
        dur = max(0.05, float(duration))

        with self.safety.ai_action_guard(sx, sy):
            self._smooth_move_both(sx, sy, min(0.25, dur * 0.5))
            if self.overlay is not None and self.config.overlay.enabled:
                self.overlay.set_status("dragging")
                self.overlay.trigger_click(sx, sy, button=button, clicks=1)
                self.overlay.move_to(ex, ey, duration=dur, wait=False)

            if not self.dry_run:
                pag = self._get_pyautogui()
                pag.mouseDown(x=sx, y=sy, button=button)
                try:
                    steps = max(5, min(35, int(dur * 45)))
                    for i in range(1, steps + 1):
                        self.safety.ensure_can_act()
                        t = ease_in_out_cubic(i / steps)
                        ix = int(round(sx + (ex - sx) * t))
                        iy = int(round(sy + (ey - sy) * t))
                        self.safety.update_expected_cursor(ix, iy)
                        pag.moveTo(ix, iy)
                        time.sleep(dur / steps)
                finally:
                    pag.mouseUp(x=ex, y=ey, button=button)

            if self.overlay is not None and self.config.overlay.enabled:
                self.overlay.set_status("acting")

        return {
            "action": "drag",
            "button": button,
            "start_pixel": {"x": sx, "y": sy},
            "end_pixel": {"x": ex, "y": ey},
            "duration": dur,
        }

    def scroll(
        self,
        x: float | None = None,
        y: float | None = None,
        dx: int = 0,
        dy: int = -3,
        coord_mode: CoordMode = "normalized",
    ) -> dict[str, Any]:
        self._preflight_check(check_active_app=True)
        px: int | None = None
        py: int | None = None
        if x is not None and y is not None:
            px, py = self.resolve_coordinates(x, y, coord_mode=coord_mode)

        with self.safety.ai_action_guard(px, py):
            if px is not None and py is not None:
                self._smooth_move_both(px, py, 0.2)
            if not self.dry_run:
                pag = self._get_pyautogui()
                if dy != 0:
                    pag.scroll(int(dy * self.config.actions.scroll_multiplier if sys.platform == "win32" else dy))
                if dx != 0 and hasattr(pag, "hscroll"):
                    pag.hscroll(int(dx))
            time.sleep(self.config.actions.action_delay_sec)

        return {"action": "scroll", "dx": dx, "dy": dy, "pixel": {"x": px, "y": py}}

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        use_clipboard: bool = True,
        press_enter: bool = False,
    ) -> dict[str, Any]:
        self._preflight_check(check_active_app=True)
        needs_clipboard = use_clipboard or (not text.isascii()) or ("\n" in text)

        with self.safety.ai_action_guard():
            method = "direct"
            if not self.dry_run:
                pag = self._get_pyautogui()
                if needs_clipboard:
                    prev = _clipboard_get() if self.config.actions.clipboard_restore else None
                    if _clipboard_set(text):
                        method = "clipboard_paste"
                        time.sleep(self.config.actions.paste_delay_sec)
                        if sys.platform == "darwin":
                            # Use osascript System Events for reliable Cmd+V across macOS apps
                            try:
                                subprocess.run(
                                    [
                                        "osascript",
                                        "-e",
                                        'tell application "System Events" to keystroke "v" using command down',
                                    ],
                                    timeout=2.0,
                                    check=True,
                                )
                            except Exception:
                                pag.hotkey("command", "v")
                        else:
                            pag.hotkey("ctrl", "v")
                        time.sleep(self.config.actions.paste_delay_sec)
                        if prev is not None and self.config.actions.clipboard_restore:
                            _clipboard_set(prev)
                    else:
                        method = "direct_fallback"
                        pag.write(text, interval=interval)
                else:
                    pag.write(text, interval=interval)

                if press_enter:
                    pag.press("enter")
            else:
                method = "clipboard_paste" if needs_clipboard else "direct"

        return {
            "action": "type_text",
            "length": len(text),
            "method": method,
            "press_enter": press_enter,
        }

    def press_hotkey(self, keys: list[str] | str) -> dict[str, Any]:
        self._preflight_check(check_active_app=True)
        if isinstance(keys, str):
            raw_keys = [part.strip() for part in keys.replace("+", " ").split() if part.strip()]
        else:
            raw_keys = [str(k).strip() for k in keys if str(k).strip()]
        normalized = [_normalize_key_name(k) for k in raw_keys]
        if not normalized:
            raise ValueError("keys must contain at least one key")

        with self.safety.ai_action_guard():
            if not self.dry_run:
                pag = self._get_pyautogui()
                if len(normalized) == 1:
                    pag.press(normalized[0])
                else:
                    pag.hotkey(*normalized)
            time.sleep(self.config.actions.action_delay_sec)

        return {"action": "press_hotkey", "keys": normalized}

    def focus_app(self, app_name: str, launch_if_not_running: bool = True) -> dict[str, Any]:
        """Switch focus to or launch an application after checking allowlist."""
        self.safety.ensure_can_act()
        self.safety.require_app_allowed(app_name)

        if self.dry_run:
            return {"action": "focus_app", "app_name": app_name, "focused": True, "dry_run": True}

        focused = False
        detail = ""

        if sys.platform == "darwin":
            res = subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to activate'],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            if res.returncode == 0:
                focused = True
                detail = "Activated via osascript"
            elif launch_if_not_running:
                res2 = subprocess.run(["open", "-a", app_name], capture_output=True, text=True, timeout=5.0)
                focused = res2.returncode == 0
                detail = "Launched via open -a" if focused else res2.stderr.strip()

        elif sys.platform == "win32":
            focused = self._focus_windows_app(app_name)
            detail = "Focused existing window" if focused else ""
            if not focused and launch_if_not_running:
                try:
                    subprocess.Popen(["cmd", "/c", "start", "", app_name], shell=False)
                    time.sleep(0.8)
                    focused = True
                    detail = "Launched via cmd start"
                except Exception as exc:
                    detail = str(exc)

        else:
            # Linux X11
            if shutil.which("wmctrl"):
                res = subprocess.run(["wmctrl", "-a", app_name], capture_output=True, text=True, timeout=2.0)
                focused = res.returncode == 0
                detail = "Focused via wmctrl"
            if not focused and shutil.which("xdotool"):
                res = subprocess.run(
                    ["xdotool", "search", "--name", app_name, "windowactivate"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                focused = res.returncode == 0
                detail = "Focused via xdotool"
            if not focused and launch_if_not_running and shutil.which(app_name):
                subprocess.Popen([app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.5)
                focused = True
                detail = f"Launched {app_name}"

        return {
            "action": "focus_app",
            "app_name": app_name,
            "focused": focused,
            "detail": detail,
            "active_app": get_active_app_name(),
        }

    @staticmethod
    def _focus_windows_app(app_name: str) -> bool:
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        target = app_name.lower().removesuffix(".exe")
        matched_hwnd: list[int] = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _enum_cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value.lower()
            proc_name = ""
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                h = kernel32.OpenProcess(0x1000, False, pid.value)
                if h:
                    try:
                        pbuf = ctypes.create_unicode_buffer(512)
                        plen = ctypes.c_ulong(512)
                        if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(plen)):
                            proc_name = os.path.basename(pbuf.value).lower().removesuffix(".exe")
                    finally:
                        kernel32.CloseHandle(h)
            if target in title or target == proc_name or target in proc_name:
                matched_hwnd.append(hwnd)
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        if matched_hwnd:
            hwnd = matched_hwnd[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        return False
