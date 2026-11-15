"""Desktop Control Panel & Launcher GUI for Antigravity Computer Use.

Provides a one-stop graphical interface to:
- Test & configure the OpenAI-compatible VLM endpoint (e.g. http://100.121.235:8000/v1)
- Run natural-language desktop automation tasks with the live virtual cursor overlay
- Preview the virtual cursor movement & click ripple demo
- Start/stop the MCP server (streamable-http) and copy/install MCP client JSON
- Check macOS/Windows permissions and trigger Emergency Stop / Resume
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from pathlib import Path
from typing import Any

import httpx

from config_loader import DEFAULT_CONFIG_PATH, PROJECT_ROOT, AppConfig
from install_mcp_config import (
    build_mcp_config_document,
    detect_claude_desktop_config_path,
    install_into_json_file,
)
from safety import check_os_permissions


def test_vlm_connection(base_url: str, api_key: str = "EMPTY", timeout: float = 5.0) -> tuple[bool, str]:
    """Check connectivity to an OpenAI-compatible /v1/models endpoint."""
    url = base_url.strip().rstrip("/")
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    headers = {"Authorization": f"Bearer {api_key or 'EMPTY'}"}
    try:
        resp = httpx.get(f"{url}/models", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id", "?") for m in data.get("data", []) if isinstance(m, dict)]
            return True, f"接続成功 (利用可能モデル: {', '.join(models[:5]) or 'OK'})"
        return False, f"HTTP {resp.status_code}: {resp.text[:160]}"
    except Exception as exc:
        return False, f"接続エラー: {exc}"


class LauncherApp:
    """Cross-platform Tkinter Control Panel (works on macOS, Windows, and Linux/Xvfb)."""

    def __init__(self, root: tk.Tk, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.root = root
        self.config_path = config_path
        self.config = AppConfig.load(config_path)

        self.root.title("Antigravity Computer Use - コントロールパネル")
        self.root.geometry("760x680")
        self.root.minsize(680, 580)

        self._mcp_proc: subprocess.Popen[str] | None = None
        self._vlm_proc: subprocess.Popen[str] | None = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Tab 1: VLM Task & Virtual Cursor ---
        tab_main = ttk.Frame(notebook)
        notebook.add(tab_main, text="🤖 AI操作・仮想カーソル")

        # VLM connection box
        vlm_frame = ttk.LabelFrame(tab_main, text="ローカルVLM接続先 (OpenAI互換API)")
        vlm_frame.pack(fill="x", **pad)

        ttk.Label(vlm_frame, text="API URL:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_base_url = tk.StringVar(value=self.config.vlm.base_url)
        ttk.Entry(vlm_frame, textvariable=self.var_base_url, width=42).grid(
            row=0, column=1, sticky="ew", padx=4, pady=4
        )
        ttk.Button(vlm_frame, text="接続テスト", command=self._on_test_vlm).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(vlm_frame, text="モデル名:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_model = tk.StringVar(value=self.config.vlm.model)
        ttk.Entry(vlm_frame, textvariable=self.var_model, width=42).grid(
            row=1, column=1, sticky="ew", padx=4, pady=4
        )
        ttk.Button(vlm_frame, text="設定保存", command=self._on_save_config).grid(row=1, column=2, padx=6, pady=4)
        vlm_frame.columnconfigure(1, weight=1)

        # Task runner box
        task_frame = ttk.LabelFrame(tab_main, text="自律操作タスク実行")
        task_frame.pack(fill="x", **pad)

        ttk.Label(task_frame, text="指示内容 (例: メモ帳を開いて『テスト』と入力):").pack(anchor="w", padx=6, pady=(4, 2))
        self.var_task = tk.StringVar(value="画面を確認して現在の状態を教えてください")
        ttk.Entry(task_frame, textvariable=self.var_task).pack(fill="x", padx=6, pady=4)

        btn_row = ttk.Frame(task_frame)
        btn_row.pack(fill="x", padx=6, pady=4)
        self.btn_run_task = ttk.Button(btn_row, text="▶ AIで実行", command=self._on_run_vlm_task)
        self.btn_run_task.pack(side="left", padx=(0, 6))
        self.btn_stop_task = ttk.Button(btn_row, text="⏹ 緊急停止", command=self._on_stop_vlm_task)
        self.btn_stop_task.pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="✨ 仮想カーソルデモ再生", command=self._on_run_cursor_demo).pack(side="right")

        # MCP server box
        mcp_frame = ttk.LabelFrame(tab_main, text="MCPサーバー連携 (Claude Desktop / Cursor / Antigravity)")
        mcp_frame.pack(fill="x", **pad)

        mcp_row = ttk.Frame(mcp_frame)
        mcp_row.pack(fill="x", padx=6, pady=6)
        self.var_mcp_status = tk.StringVar(value="停止中 (stdioモードはMCPクライアントから自動起動されます)")
        ttk.Label(mcp_row, textvariable=self.var_mcp_status).pack(side="left", fill="x", expand=True)
        self.btn_mcp_toggle = ttk.Button(
            mcp_row, text=f"HTTPサーバー起動 (:{self.config.server.port})", command=self._on_toggle_mcp_http
        )
        self.btn_mcp_toggle.pack(side="right")

        cfg_row = ttk.Frame(mcp_frame)
        cfg_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(cfg_row, text="📋 MCP設定JSONをコピー", command=self._on_copy_mcp_json).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(cfg_row, text="Claude Desktopへ自動登録", command=self._on_install_claude).pack(side="left")

        # Log viewer
        log_frame = ttk.LabelFrame(tab_main, text="実行ログ")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_box = scrolledtext.ScrolledText(log_frame, height=12, state="disabled", font=("Monospace", 9))
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

        # --- Tab 2: Safety & OS Permissions ---
        tab_safety = ttk.Frame(notebook)
        notebook.add(tab_safety, text="🛡 安全制御・OS権限")

        perm_frame = ttk.LabelFrame(tab_safety, text="OS権限ステータス")
        perm_frame.pack(fill="x", **pad)
        self.var_perm_info = tk.StringVar(value=self._format_perm_text())
        ttk.Label(perm_frame, textvariable=self.var_perm_info, justify="left", wraplength=680).pack(
            anchor="w", padx=8, pady=6
        )
        if sys.platform == "darwin":
            mac_btns = ttk.Frame(perm_frame)
            mac_btns.pack(fill="x", padx=8, pady=(0, 6))
            ttk.Button(
                mac_btns,
                text="macOS「画面収録」設定を開く",
                command=lambda: self._open_mac_privacy("Privacy_ScreenCapture"),
            ).pack(side="left", padx=(0, 6))
            ttk.Button(
                mac_btns,
                text="macOS「アクセシビリティ」設定を開く",
                command=lambda: self._open_mac_privacy("Privacy_Accessibility"),
            ).pack(side="left")

        allow_frame = ttk.LabelFrame(tab_safety, text="許可アプリ ホワイトリスト (always_allowed_apps)")
        allow_frame.pack(fill="both", expand=True, **pad)

        self.allow_listbox = tk.Listbox(allow_frame, height=10)
        self.allow_listbox.pack(fill="both", expand=True, padx=8, pady=6)
        for app in self.config.safety.always_allowed_apps:
            self.allow_listbox.insert("end", app)

        add_row = ttk.Frame(allow_frame)
        add_row.pack(fill="x", padx=8, pady=(0, 8))
        self.var_new_app = tk.StringVar()
        ttk.Entry(add_row, textvariable=self.var_new_app).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(add_row, text="追加して保存", command=self._on_add_allowed_app).pack(side="left", padx=(0, 6))
        ttk.Button(add_row, text="選択削除", command=self._on_remove_allowed_app).pack(side="left")

        self._log("準備完了。Esc 2回連打 または Ctrl+Alt+Shift+X でいつでも緊急停止できます。")

    def _log(self, msg: str) -> None:
        def _append() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"{msg}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, _append)

    @staticmethod
    def _format_perm_text() -> str:
        info = check_os_permissions()
        return json.dumps(info, ensure_ascii=False, indent=2)

    @staticmethod
    def _open_mac_privacy(pane: str) -> None:
        if sys.platform == "darwin":
            subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])

    def _on_save_config(self) -> None:
        self.config.vlm.base_url = self.var_base_url.get().strip()
        self.config.vlm.model = self.var_model.get().strip()
        self.config.save(self.config_path)
        self._log(f"[設定保存] {self.config_path} を更新しました。")

    def _on_test_vlm(self) -> None:
        url = self.var_base_url.get().strip()
        self._log(f"[VLMテスト] {url} へ接続中...")

        def _worker() -> None:
            ok, msg = test_vlm_connection(url, self.config.vlm.api_key)
            self._log(f"[VLMテスト結果] {'OK' if ok else 'NG'}: {msg}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_run_cursor_demo(self) -> None:
        self._log("[デモ] 仮想カーソルオーバーレイデモを起動します (assets/virtual_cursor.png)...")
        subprocess.Popen([sys.executable, str(PROJECT_ROOT / "cursor_overlay.py"), "--demo"])

    def _on_run_vlm_task(self) -> None:
        if self._vlm_proc is not None and self._vlm_proc.poll() is None:
            messagebox.showinfo("実行中", "すでにVLMタスクが実行中です。")
            return
        self._on_save_config()
        task = self.var_task.get().strip()
        if not task:
            return
        self._log(f"[VLM開始] タスク: {task}")
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "vlm_client.py"),
            task,
            "--config",
            str(self.config_path),
        ]
        self._vlm_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def _reader() -> None:
            assert self._vlm_proc is not None and self._vlm_proc.stdout is not None
            for line in self._vlm_proc.stdout:
                self._log(line.rstrip())
            rc = self._vlm_proc.wait()
            self._log(f"[VLM終了] exit_code={rc}")

        threading.Thread(target=_reader, daemon=True).start()

    def _on_stop_vlm_task(self) -> None:
        if self._vlm_proc is not None and self._vlm_proc.poll() is None:
            self._vlm_proc.terminate()
            self._log("[緊急停止] VLMタスクプロセスを停止しました。")

    def _on_toggle_mcp_http(self) -> None:
        if self._mcp_proc is not None and self._mcp_proc.poll() is None:
            self._mcp_proc.terminate()
            self._mcp_proc = None
            self.var_mcp_status.set("停止中")
            self.btn_mcp_toggle.configure(text=f"HTTPサーバー起動 (:{self.config.server.port})")
            self._log("[MCP] HTTPサーバーを停止しました。")
            return

        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "computer_use_mcp.py"),
            "--transport",
            "streamable-http",
            "--host",
            self.config.server.host,
            "--port",
            str(self.config.server.port),
        ]
        self._mcp_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        url = f"http://{self.config.server.host}:{self.config.server.port}/mcp"
        self.var_mcp_status.set(f"稼働中: {url}")
        self.btn_mcp_toggle.configure(text="HTTPサーバー停止")
        self._log(f"[MCP] Streamable-HTTP サーバーを起動しました: {url}")

    def _on_copy_mcp_json(self) -> None:
        doc = json.dumps(build_mcp_config_document(), ensure_ascii=False, indent=2)
        self.root.clipboard_clear()
        self.root.clipboard_append(doc)
        out = PROJECT_ROOT / "mcp_client_config.json"
        out.write_text(doc + "\n", encoding="utf-8")
        self._log(f"[MCP設定] クリップボードにコピーし、{out} にも保存しました。")

    def _on_install_claude(self) -> None:
        target = detect_claude_desktop_config_path()
        install_into_json_file(target)
        self._log(f"[MCP登録] Claude Desktop設定を更新しました: {target}")
        messagebox.showinfo("登録完了", f"Claude Desktop 設定へ登録しました:\n{target}\n\nClaude Desktopを再起動してください。")

    def _on_add_allowed_app(self) -> None:
        name = self.var_new_app.get().strip()
        if not name:
            return
        if name not in self.config.safety.always_allowed_apps:
            self.config.safety.always_allowed_apps.append(name)
            self.allow_listbox.insert("end", name)
            self.config.save(self.config_path)
            self.var_new_app.set("")
            self._log(f"[安全設定] 許可アプリを追加: {name}")

    def _on_remove_allowed_app(self) -> None:
        sel = self.allow_listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        name = self.allow_listbox.get(idx)
        self.allow_listbox.delete(idx)
        self.config.safety.always_allowed_apps = [
            a for a in self.config.safety.always_allowed_apps if a != name
        ]
        self.config.save(self.config_path)
        self._log(f"[安全設定] 許可アプリを削除: {name}")

    def _on_close(self) -> None:
        for proc in (self._mcp_proc, self._vlm_proc):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    LauncherApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
