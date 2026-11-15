"""Generate and optionally install MCP client configuration for Claude Desktop, Cursor, or Antigravity."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent


def get_python_executable() -> str:
    """Return the venv python path if present, otherwise current sys.executable."""
    if sys.platform == "win32":
        venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def build_mcp_server_entry(server_name: str = "antigravity-computer-use") -> dict[str, Any]:
    return {
        server_name: {
            "command": get_python_executable(),
            "args": [str((PROJECT_ROOT / "computer_use_mcp.py").resolve())],
        }
    }


def build_mcp_config_document(server_name: str = "antigravity-computer-use") -> dict[str, Any]:
    return {"mcpServers": build_mcp_server_entry(server_name)}


def detect_claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def install_into_json_file(target_path: Path, server_name: str = "antigravity-computer-use") -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target_path.exists():
        try:
            existing = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers.update(build_mcp_server_entry(server_name))
    existing["mcpServers"] = servers
    target_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or install MCP client config JSON")
    parser.add_argument("--write-local", action="store_true", help="Write mcp_client_config.json in repo root")
    parser.add_argument("--install-claude", action="store_true", help="Merge into Claude Desktop config")
    args = parser.parse_args()

    doc = build_mcp_config_document()
    if args.write_local:
        out = PROJECT_ROOT / "mcp_client_config.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    if args.install_claude:
        target = detect_claude_desktop_config_path()
        install_into_json_file(target)
        print(f"Installed MCP server into {target}")
    if not args.write_local and not args.install_claude:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
