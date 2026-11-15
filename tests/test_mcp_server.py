"""Integration tests for MCP server tool invocations and VLM client parser."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from computer_use_mcp import AppRuntime, create_mcp_server
from vlm_client import parse_text_tool_calls


@pytest.mark.asyncio
async def test_mcp_tools_end_to_end_dry_run() -> None:
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    rt = AppRuntime(
        enable_overlay=True,
        enable_listeners=False,
        dry_run=True,
        overlay_backend="auto" if has_display else "headless",
    )
    mcp, _ = create_mcp_server(rt)
    try:
        # 1. Screen info
        res_info = await mcp.call_tool("get_screen_info", {})
        info_data = json.loads(res_info.content[0].text)
        assert info_data["ok"] is True
        assert "monitors" in info_data

        # 2. Take screenshot & sequential video frames (when DISPLAY exists, verify real mss capture)
        if has_display:
            res_shot = await mcp.call_tool("take_screenshot", {"image_format": "png"})
            shot_meta = json.loads(res_shot.content[0].text)
            assert shot_meta["ok"] is True
            img_content = res_shot.content[1]
            raw = base64.b64decode(img_content.data)
            pil = Image.open(io.BytesIO(raw))
            assert pil.width > 0 and pil.height > 0

            res_vid = await mcp.call_tool("take_video_frames", {"frame_count": 2, "interval_sec": 0.05})
            vid_meta = json.loads(res_vid.content[0].text)
            assert vid_meta["ok"] is True
            assert vid_meta["frame_count"] == 2
            assert len(res_vid.content) == 3

        # 2b. Cursor status ('thinking') & Screen Sharing tool
        r_think = await mcp.call_tool("set_cursor_status", {"status": "thinking"})
        assert json.loads(r_think.content[0].text)["status"] == "thinking"

        r_vnc = await mcp.call_tool("connect_screen_sharing", {"host": "192.168.64.2"})
        assert json.loads(r_vnc.content[0].text)["ok"] is True

        # 3. Move cursor & click & double-click & drag & scroll
        r_move = await mcp.call_tool("move_cursor", {"x": 500, "y": 400})
        assert json.loads(r_move.content[0].text)["ok"] is True

        r_click = await mcp.call_tool("click", {"x": 500, "y": 400, "button": "left", "clicks": 2})
        click_json = json.loads(r_click.content[0].text)
        assert click_json["ok"] is True
        assert click_json["clicks"] == 2

        r_drag = await mcp.call_tool(
            "drag", {"start_x": 200, "start_y": 200, "end_x": 700, "end_y": 600, "duration": 0.1}
        )
        assert json.loads(r_drag.content[0].text)["ok"] is True

        r_scroll = await mcp.call_tool("scroll", {"x": 500, "y": 500, "dy": -5})
        assert json.loads(r_scroll.content[0].text)["ok"] is True

        # 4. Japanese text typing (clipboard paste mode) & hotkey
        r_type = await mcp.call_tool("type_text", {"text": "こんにちは、世界！", "press_enter": True})
        type_json = json.loads(r_type.content[0].text)
        assert type_json["ok"] is True
        assert type_json["method"] == "clipboard_paste"

        r_hotkey = await mcp.call_tool("press_hotkey", {"keys": ["ctrl", "c"]})
        assert json.loads(r_hotkey.content[0].text)["ok"] is True

        # 5. focus_app allowlist enforcement
        r_focus_ok = await mcp.call_tool("focus_app", {"app_name": "notepad.exe"})
        assert json.loads(r_focus_ok.content[0].text)["ok"] is True

        r_focus_blocked = await mcp.call_tool("focus_app", {"app_name": "1Password"})
        blocked_json = json.loads(r_focus_blocked.content[0].text)
        assert blocked_json["ok"] is False
        assert blocked_json["error_type"] == "app_not_allowed"

        # 6. Safety pause + resume
        rt.safety.handle_key_press("esc", now=1.0)
        rt.safety.handle_key_press("esc", now=1.2)
        r_blocked_action = await mcp.call_tool("click", {"x": 100, "y": 100})
        assert json.loads(r_blocked_action.content[0].text)["error_type"] == "safety_paused"

        r_resume = await mcp.call_tool("resume_automation", {})
        assert json.loads(r_resume.content[0].text)["resumed"] is True
    finally:
        rt.stop()


def test_vlm_fallback_tool_parsers() -> None:
    qwen_out = '<tool_call>{"name": "click", "arguments": {"x": 450, "y": 320}}</tool_call>'
    parsed = parse_text_tool_calls(qwen_out)
    assert parsed == [{"name": "click", "arguments": {"x": 450, "y": 320}}]

    uitars_out = "Thought: click search bar\nAction: left_double(start_box='(512, 128)')"
    parsed2 = parse_text_tool_calls(uitars_out)
    assert parsed2 == [{"name": "click", "arguments": {"x": 512, "y": 128, "button": "left", "clicks": 2}}]


def test_llama_cpp_tool_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from vlm_client import OpenAIComputerUseAgent

    rt = AppRuntime(enable_overlay=False, enable_listeners=False, dry_run=True)
    agent = OpenAIComputerUseAgent(rt)
    assert rt.config.vlm.base_url == "http://100.121.235.97:8000/v1"

    calls: list[dict] = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if "tools" in kwargs:
            raise RuntimeError("Error code: 400 - tools require --jinja")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))]
        )

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    resp = agent._create_completion([{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "done"
    assert len(calls) == 2
    assert "tools" in calls[0]
    assert "tools" not in calls[1]
    assert agent._use_native_tools is False
    rt.stop()
