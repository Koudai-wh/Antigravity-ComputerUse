"""Safety guardrails, app allowlist management, kill switch, and human-priority lock."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from config_loader import SafetyConfig


class SafetyAbortError(RuntimeError):
    """Raised when automation is halted by kill switch or human-priority lock."""


class AppPermissionError(PermissionError):
    """Raised when attempting to interact with a blocked or unapproved application."""


def normalize_app_name(name: str) -> str:
    """Normalize app/process names for cross-platform matching."""
    cleaned = name.strip().strip('"').strip("'")
    if "/" in cleaned or "\\" in cleaned:
        cleaned = Path(cleaned.replace("\\", "/")).name
    lowered = cleaned.lower()
    for suffix in (".app", ".exe"):
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
    return lowered.strip()


def app_matches_list(app_name: str, patterns: list[str]) -> bool:
    """Check if app_name matches any entry in patterns (normalized or substring-safe)."""
    if not app_name:
        return False
    norm_target = normalize_app_name(app_name)
    for pat in patterns:
        norm_pat = normalize_app_name(pat)
        if not norm_pat:
            continue
        if norm_target == norm_pat:
            return True
        # Match multi-word names like "Visual Studio Code" vs "Code"
        if len(norm_pat) >= 4 and (norm_pat in norm_target or norm_target in norm_pat):
            return True
    return False


def get_active_app_name() -> str | None:
    """Detect the currently focused application/process name cross-platform."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSWorkspace  # type: ignore

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is not None:
                return str(app.localizedName() or app.bundleIdentifier() or "")
        except Exception:
            pass
        try:
            out = subprocess.check_output(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                text=True,
                timeout=2.0,
                stderr=subprocess.DEVNULL,
            ).strip()
            return out or None
        except Exception:
            return None

    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
                if handle:
                    try:
                        buf_len = ctypes.c_ulong(512)
                        buf = ctypes.create_unicode_buffer(512)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len)):
                            return Path(buf.value).name
                    finally:
                        kernel32.CloseHandle(handle)
            title_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title_buf, 256)
            return title_buf.value or None
        except Exception:
            return None

    # Linux X11 fallback
    try:
        wid = subprocess.check_output(
            ["xdotool", "getactivewindow"],
            text=True,
            timeout=1.5,
            stderr=subprocess.DEVNULL,
        ).strip()
        if wid:
            pid = subprocess.check_output(
                ["xdotool", "getwindowpid", wid],
                text=True,
                timeout=1.5,
                stderr=subprocess.DEVNULL,
            ).strip()
            if pid and Path(f"/proc/{pid}/comm").exists():
                return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def check_os_permissions() -> dict[str, Any]:
    """Check OS-level screen recording and accessibility permissions."""
    if sys.platform == "darwin":
        screen_recording = None
        accessibility = None
        try:
            from Quartz import CGPreflightScreenCaptureAccess  # type: ignore

            screen_recording = bool(CGPreflightScreenCaptureAccess())
        except Exception:
            pass
        try:
            from ApplicationServices import AXIsProcessTrusted  # type: ignore

            accessibility = bool(AXIsProcessTrusted())
        except Exception:
            pass
        return {
            "platform": "darwin",
            "screen_recording_granted": screen_recording,
            "accessibility_granted": accessibility,
            "guidance": (
                "macOS System Settings -> Privacy & Security -> Screen Recording and Accessibility "
                "must allow the Python/Terminal/MCP host executable."
            ),
        }
    if sys.platform == "win32":
        return {
            "platform": "win32",
            "interactive_desktop": True,
            "guidance": "Run inside the active user desktop session (not Session 0 service) for UTM/VM control.",
        }
    return {
        "platform": sys.platform,
        "display": os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or None,
    }


def _gui_confirm_app(app_name: str, timeout_sec: float = 20.0) -> str:
    """Show a native confirmation prompt for an unlisted application.

    Returns one of: 'allow_once', 'always_allow', 'deny'.
    """
    result = {"choice": "deny"}

    # Try tkinter dialog (built into Python stdlib on macOS/Windows/Linux)
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("Antigravity Computer Use - App Permission")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = tk.Frame(root, padx=18, pady=14)
        frame.pack()

        msg = (
            f"AIエージェントが未許可のアプリケーションを操作しようとしています:\n\n"
            f"  アプリ名: {app_name}\n\n"
            f"このアプリケーションへの操作を許可しますか？"
        )
        tk.Label(frame, text=msg, justify="left", font=("Sans", 10)).pack(pady=(0, 12))

        btn_row = tk.Frame(frame)
        btn_row.pack(fill="x")

        def choose(val: str) -> None:
            result["choice"] = val
            root.destroy()

        tk.Button(btn_row, text="今回のみ許可", width=13, command=lambda: choose("allow_once")).pack(
            side="left", padx=4
        )
        tk.Button(btn_row, text="常に許可", width=12, command=lambda: choose("always_allow")).pack(
            side="left", padx=4
        )
        tk.Button(btn_row, text="ブロック", width=10, command=lambda: choose("deny")).pack(side="right", padx=4)

        root.after(int(max(1.0, timeout_sec) * 1000), lambda: choose("deny"))
        root.mainloop()
        return result["choice"]
    except Exception:
        return "deny"


@dataclass
class SafetyState:
    paused: bool = False
    emergency_stopped: bool = False
    pause_reason: str | None = None
    paused_at: float | None = None
    last_human_event_at: float | None = None
    human_events_count: int = 0
    session_allowed_apps: set[str] = field(default_factory=set)


class SafetyMonitor:
    """Monitors human input for kill switch / human-priority lock and enforces app allowlists."""

    def __init__(
        self,
        config: SafetyConfig,
        on_pause_callback: Callable[[str], None] | None = None,
        confirm_callback: Callable[[str, float], str] | None = None,
        persist_allow_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.state = SafetyState()
        self._lock = threading.RLock()
        self._abort_event = threading.Event()
        self._on_pause_callback = on_pause_callback
        self._confirm_callback = confirm_callback or _gui_confirm_app
        self._persist_allow_callback = persist_allow_callback

        # Synthetic input tracking to prevent AI's own actions from triggering human lock
        self._ai_action_depth = 0
        self._suppress_until = 0.0
        self._expected_pos: tuple[float, float] | None = None
        self._last_ai_pos: tuple[float, float] | None = None

        # Key tracking for Esc burst and emergency hotkey
        self._esc_timestamps: list[float] = []
        self._pressed_modifiers: set[str] = set()

        # pynput listeners (optional if display is unavailable)
        self._mouse_listener: Any = None
        self._keyboard_listener: Any = None
        self._listeners_started = False

    def start_listeners(self) -> bool:
        """Start background mouse/keyboard listeners for kill-switch and human-priority lock."""
        if self._listeners_started:
            return True
        if not (self.config.kill_switch.enabled or self.config.human_priority_lock.enabled):
            return False
        try:
            from pynput import keyboard, mouse  # type: ignore

            self._mouse_listener = mouse.Listener(
                on_move=self._on_pynput_move,
                on_click=self._on_pynput_click,
                on_scroll=self._on_pynput_scroll,
            )
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_pynput_key_press,
                on_release=self._on_pynput_key_release,
            )
            self._mouse_listener.daemon = True
            self._keyboard_listener.daemon = True
            self._mouse_listener.start()
            self._keyboard_listener.start()
            self._listeners_started = True
            return True
        except Exception:
            self._listeners_started = False
            return False

    def stop_listeners(self) -> None:
        for listener in (self._mouse_listener, self._keyboard_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._mouse_listener = None
        self._keyboard_listener = None
        self._listeners_started = False

    # --- pynput event adapters ---
    @staticmethod
    def _normalize_pynput_key(key: Any) -> str:
        name = getattr(key, "name", None)
        if name:
            n = str(name).lower()
            if n in ("ctrl_l", "ctrl_r", "ctrl"):
                return "ctrl"
            if n in ("alt_l", "alt_r", "alt", "alt_gr"):
                return "alt"
            if n in ("shift_l", "shift_r", "shift"):
                return "shift"
            if n in ("cmd", "cmd_l", "cmd_r", "super"):
                return "cmd"
            return n
        char = getattr(key, "char", None)
        if char:
            return str(char).lower()
        return str(key).lower()

    def _on_pynput_move(self, x: int, y: int) -> None:
        self.handle_mouse_move(float(x), float(y))

    def _on_pynput_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if pressed:
            self.handle_mouse_click(float(x), float(y))

    def _on_pynput_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.handle_mouse_click(float(x), float(y))

    def _on_pynput_key_press(self, key: Any) -> None:
        self.handle_key_press(self._normalize_pynput_key(key))

    def _on_pynput_key_release(self, key: Any) -> None:
        self.handle_key_release(self._normalize_pynput_key(key))

    # --- Deterministic core handlers (directly unit-testable) ---
    def _trigger_pause(self, reason: str, emergency: bool = False) -> None:
        cb: Callable[[str], None] | None = None
        with self._lock:
            already_paused = self.state.paused and (not emergency or self.state.emergency_stopped)
            self.state.paused = True
            if emergency:
                self.state.emergency_stopped = True
            self.state.pause_reason = reason
            self.state.paused_at = time.monotonic()
            self._abort_event.set()
            if not already_paused:
                cb = self._on_pause_callback
        if cb is not None:
            try:
                cb(reason)
            except Exception:
                pass

    def _is_ai_suppressed(self) -> bool:
        return self._ai_action_depth > 0 or time.monotonic() < self._suppress_until

    def handle_key_press(self, key_name: str, now: float | None = None) -> None:
        now_t = time.monotonic() if now is None else now
        k = key_name.lower()
        if k in ("ctrl", "alt", "shift", "cmd"):
            self._pressed_modifiers.add(k)

        # 1. Kill switch ALWAYS takes precedence, even during AI actions
        if self.config.kill_switch.enabled:
            if k in ("esc", "escape"):
                window = self.config.kill_switch.esc_window_sec
                self._esc_timestamps = [t for t in self._esc_timestamps if (now_t - t) <= window]
                self._esc_timestamps.append(now_t)
                if len(self._esc_timestamps) >= max(1, self.config.kill_switch.esc_press_count):
                    self._esc_timestamps.clear()
                    self._trigger_pause("kill_switch_esc", emergency=True)
                    return

            hotkey = [p.lower() for p in self.config.kill_switch.emergency_hotkey]
            if hotkey:
                target_key = hotkey[-1]
                req_mods = set(hotkey[:-1])
                if k == target_key and req_mods.issubset(self._pressed_modifiers):
                    self._trigger_pause("kill_switch_hotkey", emergency=True)
                    return

        # 2. Human-priority lock on manual key press outside AI action window
        if self.config.human_priority_lock.enabled and not self._is_ai_suppressed():
            with self._lock:
                self.state.last_human_event_at = now_t
                self.state.human_events_count += 1
            self._trigger_pause(f"human_key_press:{k}", emergency=False)

    def handle_key_release(self, key_name: str) -> None:
        self._pressed_modifiers.discard(key_name.lower())

    def handle_mouse_move(self, x: float, y: float, now: float | None = None) -> None:
        if not self.config.human_priority_lock.enabled:
            return
        now_t = time.monotonic() if now is None else now
        thresh = max(4, self.config.human_priority_lock.mouse_move_threshold_px)

        with self._lock:
            if self._is_ai_suppressed():
                # Even during AI action, if physical cursor jumps far away from AI's expected target,
                # a human grabbed the mouse -> abort immediately!
                if self._expected_pos is not None:
                    ex, ey = self._expected_pos
                    dist = ((x - ex) ** 2 + (y - ey) ** 2) ** 0.5
                    if dist > max(48.0, thresh * 2.5):
                        self.state.last_human_event_at = now_t
                        self.state.human_events_count += 1
                        self._trigger_pause("human_mouse_override_during_action", emergency=False)
                return

            # Outside AI action: check movement distance from last AI cursor position
            ref = self._last_ai_pos
            if ref is not None:
                dist = ((x - ref[0]) ** 2 + (y - ref[1]) ** 2) ** 0.5
                if dist < thresh:
                    return
            self.state.last_human_event_at = now_t
            self.state.human_events_count += 1

        self._trigger_pause("human_mouse_move", emergency=False)

    def handle_mouse_click(self, x: float, y: float, now: float | None = None) -> None:
        if not self.config.human_priority_lock.enabled:
            return
        if self._is_ai_suppressed():
            return
        now_t = time.monotonic() if now is None else now
        with self._lock:
            self.state.last_human_event_at = now_t
            self.state.human_events_count += 1
        self._trigger_pause("human_mouse_click", emergency=False)

    def update_expected_cursor(self, x: float, y: float) -> None:
        with self._lock:
            self._expected_pos = (float(x), float(y))
            self._last_ai_pos = (float(x), float(y))

    @contextmanager
    def ai_action_guard(
        self, expected_x: float | None = None, expected_y: float | None = None
    ) -> Iterator[None]:
        """Guard an AI-driven GUI action against pause/kill-switch and suppress self-events."""
        self.ensure_can_act()
        with self._lock:
            self._ai_action_depth += 1
            if expected_x is not None and expected_y is not None:
                self._expected_pos = (float(expected_x), float(expected_y))
                self._last_ai_pos = (float(expected_x), float(expected_y))
        try:
            yield
            self.ensure_can_act()
        finally:
            with self._lock:
                self._ai_action_depth = max(0, self._ai_action_depth - 1)
                self._suppress_until = time.monotonic() + 0.12

    def ensure_can_act(self) -> None:
        """Raise SafetyAbortError if automation is paused or emergency-stopped."""
        with self._lock:
            if self.state.paused:
                auto_sec = self.config.human_priority_lock.auto_resume_after_idle_sec
                if (
                    not self.state.emergency_stopped
                    and auto_sec > 0
                    and self.state.last_human_event_at is not None
                    and (time.monotonic() - self.state.last_human_event_at) >= auto_sec
                ):
                    self.resume()
                    return
                raise SafetyAbortError(
                    f"Automation paused ({self.state.pause_reason}). "
                    "Call resume_automation after human control finishes."
                )

    def resume(self) -> dict[str, Any]:
        with self._lock:
            self.state.paused = False
            self.state.emergency_stopped = False
            self.state.pause_reason = None
            self.state.paused_at = None
            self._abort_event.clear()
        return self.get_status()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "paused": self.state.paused,
                "emergency_stopped": self.state.emergency_stopped,
                "pause_reason": self.state.pause_reason,
                "human_events_count": self.state.human_events_count,
                "listeners_active": self._listeners_started,
                "enforce_app_allowlist": self.config.enforce_app_allowlist,
                "unlisted_app_policy": self.config.unlisted_app_policy,
                "session_allowed_apps": sorted(self.state.session_allowed_apps),
            }

    # --- App allowlist guardrail ---
    def check_app_allowed(self, app_name: str | None, prompt_if_unlisted: bool = True) -> tuple[bool, str]:
        """Verify whether operating on `app_name` is permitted by policy."""
        if not app_name:
            if self.config.enforce_app_allowlist and self.config.unlisted_app_policy == "block":
                return False, "Active application could not be identified under strict block policy."
            return True, "No active app restriction triggered"

        if app_matches_list(app_name, self.config.blocked_apps):
            return False, f"Application '{app_name}' is explicitly blocked in config.blocked_apps."

        if not self.config.enforce_app_allowlist:
            return True, "Allowlist enforcement disabled"

        if app_matches_list(app_name, self.config.always_allowed_apps) or app_matches_list(
            app_name, list(self.state.session_allowed_apps)
        ):
            return True, f"Application '{app_name}' is in allowlist."

        policy = self.config.unlisted_app_policy
        if policy == "allow":
            return True, f"Application '{app_name}' allowed by unlisted_app_policy='allow'."
        if policy == "block" or not prompt_if_unlisted:
            return (
                False,
                f"Application '{app_name}' is not in always_allowed_apps (policy={policy}).",
            )

        # policy == "prompt"
        choice = self._confirm_callback(app_name, self.config.confirmation_timeout_sec)
        if choice == "always_allow":
            self.state.session_allowed_apps.add(app_name)
            if self._persist_allow_callback is not None:
                try:
                    self._persist_allow_callback(app_name)
                except Exception:
                    pass
            return True, f"User granted permanent permission for '{app_name}'."
        if choice == "allow_once":
            self.state.session_allowed_apps.add(app_name)
            return True, f"User granted session permission for '{app_name}'."
        return False, f"User denied or timed out permission prompt for '{app_name}'."

    def require_app_allowed(self, app_name: str | None) -> None:
        allowed, reason = self.check_app_allowed(app_name)
        if not allowed:
            raise AppPermissionError(reason)
