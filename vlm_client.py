"""OpenAI-compatible VLM Agent Bridge for Qwen2.5-VL / UI-TARS / Local LLM Endpoints.

Connects to an OpenAI-compatible chat completions endpoint (e.g. http://100.121.235:8000/v1)
and drives the Antigravity Computer Use runtime in a screenshot -> reason -> act loop.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any

from openai import OpenAI

from computer_use_mcp import AppRuntime
from config_loader import VLMConfig
from safety import AppPermissionError, SafetyAbortError

COMPUTER_USE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Move the virtual AI cursor to (x, y) and click.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "Normalized X coordinate (0-1000)"},
                    "y": {"type": "number", "description": "Normalized Y coordinate (0-1000)"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                    "clicks": {"type": "integer", "enum": [1, 2], "default": 1},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_cursor",
            "description": "Smoothly move the virtual AI cursor to (x, y).",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "Normalized X (0-1000)"},
                    "y": {"type": "number", "description": "Normalized Y (0-1000)"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag",
            "description": "Drag from (start_x, start_y) to (end_x, end_y) in normalized 0-1000 coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_x": {"type": "number"},
                    "start_y": {"type": "number"},
                    "end_x": {"type": "number"},
                    "end_y": {"type": "number"},
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll at optional (x, y). Negative dy scrolls down, positive scrolls up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "dx": {"type": "integer", "default": 0},
                    "dy": {"type": "integer", "default": -3},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Input text (supports Japanese/Unicode via clipboard paste).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_hotkey",
            "description": "Press a key or hotkey combination, e.g. ['ctrl', 'c'] or ['command', 'space'].",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_app",
            "description": "Launch or focus an allowed application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait briefly for UI transitions.",
            "parameters": {
                "type": "object",
                "properties": {"seconds": {"type": "number", "default": 1.0}},
            },
        },
    },
]

SYSTEM_PROMPT = """You are a precise Desktop Computer Use agent.
You see screenshots of the user's screen and control the desktop via tool calls.
All coordinates use a normalized 0-1000 coordinate system where (0, 0) is top-left and (1000, 1000) is bottom-right.
Always inspect the latest screenshot carefully before choosing coordinates.
If native OpenAI tool calling is unavailable, output a single JSON tool call wrapped in <tool_call>...</tool_call>, e.g.:
<tool_call>{"name": "click", "arguments": {"x": 500, "y": 300, "button": "left", "clicks": 1}}</tool_call>
Available tools: click(x, y, button, clicks), move_cursor(x, y), drag(start_x, start_y, end_x, end_y), scroll(x, y, dx, dy), type_text(text, press_enter), press_hotkey(keys), focus_app(app_name), wait(seconds).
When the user's goal is completely achieved, respond with a concise final summary without calling any tools.
"""


def normalize_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    if not u.endswith("/v1"):
        u = f"{u}/v1"
    return u


def parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Fallback parser for VLMs (Qwen2.5-VL / UI-TARS / llama.cpp) that emit JSON or Action: text."""
    calls: list[dict[str, Any]] = []
    # 1. <tool_call>{...}</tool_call> or ```json {...} ```
    for match in re.finditer(r"(?:<tool_call>|```json)\s*(\{.*?\})\s*(?:</tool_call>|```)", content, re.DOTALL):
        try:
            obj = json.loads(match.group(1))
            name = obj.get("name") or obj.get("action")
            args = obj.get("arguments") or obj.get("args") or {}
            if isinstance(name, str) and isinstance(args, dict):
                calls.append({"name": name, "arguments": args})
        except Exception:
            pass
    if calls:
        return calls

    # 2. UI-TARS style: Action: click(start_box='(512,340)')
    box_match = re.search(
        r"Action:\s*(click|left_double|right_single|move)\s*\(\s*(?:start_box|point)=['\"]?\(?\s*(\d+)\s*,\s*(\d+)\s*\)?['\"]?",
        content,
        re.IGNORECASE,
    )
    if box_match:
        act = box_match.group(1).lower()
        x, y = int(box_match.group(2)), int(box_match.group(3))
        if act == "move":
            calls.append({"name": "move_cursor", "arguments": {"x": x, "y": y}})
        elif act == "left_double":
            calls.append({"name": "click", "arguments": {"x": x, "y": y, "button": "left", "clicks": 2}})
        elif act == "right_single":
            calls.append({"name": "click", "arguments": {"x": x, "y": y, "button": "right", "clicks": 1}})
        else:
            calls.append({"name": "click", "arguments": {"x": x, "y": y, "button": "left", "clicks": 1}})
    return calls


def fetch_available_models(base_url: str, api_key: str = "EMPTY", timeout: float = 5.0) -> tuple[bool, list[str], str]:
    """Fetch available models from an OpenAI-compatible /v1/models endpoint."""
    import httpx

    url = normalize_base_url(base_url)
    headers = {"Authorization": f"Bearer {api_key or 'EMPTY'}"}
    try:
        resp = httpx.get(f"{url}/models", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            models = [str(m.get("id", "")) for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
            return True, models, f"Connected ({len(models)} models)"
        return False, [], f"HTTP {resp.status_code}: {resp.text[:160]}"
    except Exception as exc:
        return False, [], str(exc)


def send_chat_completion(
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str = "EMPTY",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout_seconds: float = 120.0,
) -> str:
    """Send a multi-turn multimodal Chat completion request to an OpenAI-compatible server."""
    client = OpenAI(
        base_url=normalize_base_url(base_url),
        api_key=api_key or "EMPTY",
        timeout=timeout_seconds,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()


class OpenAIComputerUseAgent:
    """Executes multi-step desktop tasks using an OpenAI-compatible VLM endpoint (llama.cpp / vLLM / etc.)."""

    def __init__(
        self,
        runtime: AppRuntime,
        vlm_config: VLMConfig | None = None,
        on_status: Any = None,
        on_step: Any = None,
    ) -> None:
        self.runtime = runtime
        self.vlm = vlm_config or runtime.config.vlm
        self.on_status = on_status
        self.on_step = on_step
        self.client = OpenAI(
            base_url=normalize_base_url(self.vlm.base_url),
            api_key=self.vlm.api_key or "EMPTY",
            timeout=self.vlm.timeout_seconds,
        )
        self._use_native_tools = True

    def _notify_status(self, state: str, detail: str = "") -> None:
        if self.on_status is not None:
            try:
                self.on_status(state, detail)
            except TypeError:
                try:
                    self.on_status(state)
                except Exception:
                    pass
            except Exception:
                pass

    def _notify_step(self, entry: dict[str, Any]) -> None:
        if self.on_step is not None:
            try:
                self.on_step(entry)
            except Exception:
                pass

    def execute_action(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ctrl = self.runtime.controller
        coord_mode = "pixel" if self.vlm.coordinate_system == "pixel" else "normalized"

        if name == "click":
            return ctrl.click(
                x=args.get("x"),
                y=args.get("y"),
                button=args.get("button", "left"),
                clicks=int(args.get("clicks", 1)),
                coord_mode=coord_mode,
            )
        if name == "move_cursor":
            return ctrl.move_cursor(x=args["x"], y=args["y"], coord_mode=coord_mode)
        if name == "drag":
            return ctrl.drag(
                start_x=args["start_x"],
                start_y=args["start_y"],
                end_x=args["end_x"],
                end_y=args["end_y"],
                coord_mode=coord_mode,
            )
        if name == "scroll":
            return ctrl.scroll(
                x=args.get("x"),
                y=args.get("y"),
                dx=int(args.get("dx", 0)),
                dy=int(args.get("dy", -3)),
                coord_mode=coord_mode,
            )
        if name == "type_text":
            return ctrl.type_text(text=args["text"], press_enter=bool(args.get("press_enter", False)))
        if name == "press_hotkey":
            return ctrl.press_hotkey(keys=args["keys"])
        if name == "focus_app":
            return ctrl.focus_app(app_name=args["app_name"])
        if name == "wait":
            secs = max(0.0, min(15.0, float(args.get("seconds", 1.0))))
            time.sleep(secs)
            return {"action": "wait", "seconds": secs}
        raise ValueError(f"Unknown action: {name}")

    def _create_completion(self, messages: list[dict[str, Any]]) -> Any:
        """Call chat.completions.create and fall back cleanly if llama.cpp lacks --jinja tools support."""
        if self._use_native_tools:
            try:
                return self.client.chat.completions.create(
                    model=self.vlm.model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=COMPUTER_USE_TOOLS,  # type: ignore[arg-type]
                    max_tokens=self.vlm.max_tokens,
                    temperature=self.vlm.temperature,
                )
            except Exception as exc:
                err_txt = str(exc).lower()
                if any(tok in err_txt for tok in ("tool", "jinja", "400", "422", "unsupported", "template")):
                    self._use_native_tools = False
                else:
                    raise

        return self.client.chat.completions.create(
            model=self.vlm.model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=self.vlm.max_tokens,
            temperature=self.vlm.temperature,
        )

    def run_task(self, instruction: str, max_steps: int = 12) -> dict[str, Any]:
        self.runtime.start()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]
        history: list[dict[str, Any]] = []
        target = self.vlm.capture_target

        try:
            for step in range(1, max_steps + 1):
                try:
                    self.runtime.safety.ensure_can_act()
                except (SafetyAbortError, AppPermissionError) as safety_exc:
                    self._notify_status("paused", str(safety_exc))
                    return {
                        "ok": False,
                        "steps": step - 1,
                        "stopped_reason": str(safety_exc),
                        "history": history,
                    }

                self._notify_status("capturing", f"Capturing screen (Step {step}/{max_steps})...")
                content_parts: list[dict[str, Any]] = []
                try:
                    if self.vlm.video_mode and self.vlm.video_frames > 1:
                        seq = self.runtime.controller.take_video_frames(
                            frame_count=self.vlm.video_frames,
                            interval_sec=self.vlm.video_interval_sec,
                            target=target,
                            image_format="jpeg",
                            max_width=self.vlm.video_max_width,
                        )
                        latest_meta = seq["frames"][-1]["metadata"] if seq["frames"] else {}
                        content_parts.append(
                            {
                                "type": "text",
                                "text": (
                                    f"Step {step}/{max_steps} ({seq['frame_count']} sequential video frames at "
                                    f"{seq['interval_sec']}s interval, newest last; target={seq['target']}, "
                                    f"active_app={latest_meta.get('active_app')}). Decide the next action or summarize completion."
                                ),
                            }
                        )
                        for fr in seq["frames"]:
                            content_parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{fr['mime_type']};base64,{fr['base64']}"},
                                }
                            )
                    else:
                        shot = self.runtime.controller.take_screenshot(target=target)
                        data_url = f"data:{shot['mime_type']};base64,{shot['base64']}"
                        content_parts.append(
                            {
                                "type": "text",
                                "text": (
                                    f"Step {step}/{max_steps}. Target: {shot['metadata'].get('target')}, "
                                    f"Active app: {shot['metadata'].get('active_app')}. "
                                    "Decide the next action or summarize completion."
                                ),
                            }
                        )
                        content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
                except (SafetyAbortError, AppPermissionError) as safety_exc:
                    self._notify_status("paused", str(safety_exc))
                    return {
                        "ok": False,
                        "steps": step,
                        "stopped_reason": str(safety_exc),
                        "history": history,
                    }

                messages.append({"role": "user", "content": content_parts})

                # Show spinning "thinking" loader around virtual cursor while VLM reasons
                if self.runtime.config.overlay.enabled:
                    self.runtime.overlay.set_status("thinking")
                self._notify_status("thinking", f"Thinking ({self.vlm.model})...")
                try:
                    resp = self._create_completion(messages)
                finally:
                    if self.runtime.config.overlay.enabled and not self.runtime.safety.state.paused:
                        self.runtime.overlay.set_status("acting")

                msg = resp.choices[0].message
                tool_calls = getattr(msg, "tool_calls", None) or []

                parsed_calls: list[tuple[str | None, str, dict[str, Any]]] = []
                if tool_calls:
                    messages.append(msg.model_dump(exclude_none=True))
                    for tc in tool_calls:
                        args = json.loads(tc.function.arguments or "{}")
                        parsed_calls.append((tc.id, tc.function.name, args))
                else:
                    text = msg.content or ""
                    messages.append({"role": "assistant", "content": text})
                    fallback = parse_text_tool_calls(text)
                    if not fallback:
                        return {"ok": True, "steps": step, "final_response": text, "history": history}
                    for fc in fallback:
                        parsed_calls.append((None, fc["name"], fc["arguments"]))

                for call_id, name, args in parsed_calls:
                    self._notify_status("acting", f"{name}({json.dumps(args, ensure_ascii=False)[:48]})...")
                    try:
                        result = {"ok": True, **self.execute_action(name, args)}
                    except (SafetyAbortError, AppPermissionError) as safety_exc:
                        self._notify_status("paused", str(safety_exc))
                        return {
                            "ok": False,
                            "steps": step,
                            "stopped_reason": str(safety_exc),
                            "history": history,
                        }
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}

                    step_entry = {"step": step, "tool": name, "args": args, "result": result}
                    history.append(step_entry)
                    self._notify_step(step_entry)
                    if call_id is not None:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": json.dumps(result, ensure_ascii=False),
                            }
                        )
                    else:
                        messages.append(
                            {
                                "role": "user",
                                "content": f"Tool `{name}` result: {json.dumps(result, ensure_ascii=False)}",
                            }
                        )
        finally:
            if self.runtime.safety.state.paused:
                self._notify_status("paused", self.runtime.safety.state.pause_reason or "Paused")
            else:
                if self.runtime.config.overlay.enabled:
                    self.runtime.overlay.set_status("idle")
                self._notify_status("idle", "Completed")

        return {"ok": True, "steps": max_steps, "final_response": "Reached max_steps.", "history": history}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OpenAI-compatible VLM Computer Use task")
    parser.add_argument("task", help="Task instruction for the VLM agent")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--base-url", default=None, help="Override VLM base URL (e.g. http://100.121.235.97:8000/v1)")
    parser.add_argument("--model", default=None, help="Override VLM model name")
    parser.add_argument("--target", choices=["display", "active_window", "screen_sharing"], default=None)
    parser.add_argument("--video", dest="video_mode", action="store_true", default=None, help="Send sequential video frames")
    parser.add_argument("--no-video", dest="video_mode", action="store_false", help="Send single screenshot per step")
    parser.add_argument("--frames", type=int, default=None, help="Number of frames per step in video mode")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()

    runtime = AppRuntime(config_path=args.config)
    if args.base_url:
        runtime.config.vlm.base_url = args.base_url
    if args.model:
        runtime.config.vlm.model = args.model
    if args.target:
        runtime.config.vlm.capture_target = args.target
    if args.video_mode is not None:
        runtime.config.vlm.video_mode = args.video_mode
    if args.frames is not None:
        runtime.config.vlm.video_frames = args.frames

    agent = OpenAIComputerUseAgent(runtime)
    try:
        res = agent.run_task(args.task, max_steps=args.max_steps)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
