# Antigravity Computer Use (Local VLM + MCP Desktop Automation)

OpenAIの「Computer Use」仕様に準拠した、ローカルVLM（OpenAI互換API：`Qwen2.5-VL-7B` / `UI-TARS` 等）および **MCP (Model Context Protocol)** 対応のクロスプラットフォーム・デスクトップ自動操作プラグインです。

AIが操作を行う際は、**`assets/virtual_cursor.png` を用いた透過・最前面・クリック透過の仮想カーソルオーバーレイ**が滑らかに移動し、クリック時の波紋・拡大縮小エフェクトによって人間がリアルタイムで操作位置を視認できます。

---

## 一番カンタンな開き方（ダブルクリック起動）

1. **リポジトリを取得する**
   ```bash
   git clone https://github.com/Koudai-wh/Antigravity-ComputerUse.git
   ```
   （または GitHub の `Code -> Download ZIP` で解凍）
2. **そのままダブルクリックして開く**
   - **macOS**: **`launch_mac.command`** をダブルクリック（※初回のみ右クリック→「開く」で実行）
   - **Windows (UTM仮想マシン含む)**: **`launch_windows.bat`** をダブルクリック
   - **Linux**: **`./launch_linux.sh`** を実行

初回起動時は自動で `.venv` 作成＋依存インストールを行い、**コントロールパネル (`launcher_gui.py`)** が開きます。コントロールパネルから以下がワンクリックで実行できます：
- **✨ 仮想カーソルデモ再生**（`assets/virtual_cursor.png` の移動・波紋エフェクトを即確認）
- **🔗 ローカルVLM接続テスト & タスク実行**（`http://100.121.235.97:8000/v1` 等）
- **📋 MCP設定JSONコピー / Claude Desktop自動登録**
- **🛡 macOS「画面収録 / アクセシビリティ」設定画面を直接開く**

---

## ディレクトリ構成

```text
Antigravity-ComputerUse/
├── assets/
│   └── virtual_cursor.png        # AI専用仮想カーソル画像（アルファ透過PNG）
├── tests/
│   ├── test_cursor_overlay_visual.py  # 仮想カーソル移動・クリック視覚エフェクト単体テスト
│   ├── test_mcp_server.py             # MCPツール統合テスト・VLMパーサーテスト
│   └── test_safety_and_coords.py      # 座標変換・アプリ許可・キルスイッチ・人間優先ロックテスト
├── computer_use_mcp.py           # MCPサーバー本体（stdio / sse / streamable-http）
├── cursor_overlay.py             # 仮想カーソルオーバーレイ（PySide6 / Tkinter / Headless + IPC）
├── gui_controller.py             # 画面キャプチャ(mss)・座標変換(0-1000)・日本語入力・アプリ制御
├── safety.py                     # アプリ許可ホワイトリスト・キルスイッチ・人間優先ロック
├── vlm_client.py                 # OpenAI互換VLM（http://100.121.235 等）自律ループクライアント
├── config.json                   # サーバー・VLM・オーバーレイ・安全制御設定
├── config_loader.py              # Pydantic設定ローダー（JSON / TOML対応）
├── requirements.txt              # 依存パッケージ一覧
└── pyproject.toml                # pytest設定・パッケージメタデータ
```

---

## セットアップ手順

### 1. 仮想環境の作成と依存パッケージのインストール

```bash
python3 -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

(`uv` を使用する場合: `uv venv .venv && uv pip install -r requirements.txt`)

### 2. OS別の権限・動作環境設定

#### macOS の場合
1. **システム設定 → プライバシーとセキュリティ → 画面収録 (Screen Recording)** で、実行するターミナル / Python / MCPクライアント（Cursor, Claude Desktop 等）を許可してください。
2. **システム設定 → プライバシーとセキュリティ → アクセシビリティ (Accessibility)** でも同様に許可してください。
3. 日本語入力は `pbcopy` + `Command+V` を使用して文字化け・IME干渉なしで貼り付けます。

#### Windows (UTM仮想マシン上のアクティブデスクトップを含む) の場合
- サービス（Session 0）ではなく、**ログイン済みのアクティブデスクトップセッション**上で実行してください。
- `gui_controller.py` は起動時に Per-Monitor DPI Awareness (`SetProcessDpiAwarenessContext(-4)`) を有効化し、高DPI環境でも `mss` と `pyautogui` と `PySide6` の座標ズレを防ぎます。
- 仮想カーソルオーバーレイは `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE` を適用し、完全にクリック透過・最前面表示されます。

---

## 使い方

### 1. MCPサーバーとして起動する

```bash
python computer_use_mcp.py
```

HTTP / SSE で公開する場合:

```bash
python computer_use_mcp.py --transport streamable-http --host 127.0.0.1 --port 8765
```

#### MCPクライアント設定例 (`claude_desktop_config.json` 等)

```json
{
  "mcpServers": {
    "antigravity-computer-use": {
      "command": "/path/to/Antigravity-ComputerUse/.venv/bin/python",
      "args": ["/path/to/Antigravity-ComputerUse/computer_use_mcp.py"]
    }
  }
}
```

### 2. ローカルVLM (OpenAI互換API) に直接接続して自律操作させる

`config.json` の `vlm` セクションを設定します（例: `http://100.121.235.97:8000/v1` / `Qwen2.5-VL-7B-Instruct` や `UI-TARS`）:

```bash
python vlm_client.py "メモ帳を開いて『テスト入力』と書いてください" \
  --base-url http://100.121.235.97:8000/v1 \
  --model Qwen2.5-VL-7B-Instruct
```

---

## 提供されるMCPツール一覧

| ツール名 | 機能概要 |
|---|---|
| `take_screenshot` | ディスプレイまたはアクティブウィンドウを高速キャプチャし、Base64画像＋解像度メタデータを返却 |
| `move_cursor` | 正規化座標 (0〜1000) または実ピクセル座標へ仮想カーソル＆OSカーソルを滑らかに移動 |
| `click` | 左クリック / 右クリック / ダブルクリック（クリック時に波紋＆拡大縮小エフェクトを表示） |
| `drag` | 開始座標から終了座標へのドラッグ＆ドロップ |
| `scroll` | 指定座標での上下・左右スクロール |
| `type_text` | テキスト入力（日本語・マルチラインはクリップボード貼り付けで確実に入力） |
| `press_hotkey` | 修飾キーやショートカット送信（例: `["ctrl", "c"]`, `["command", "space"]`） |
| `focus_app` | 許可されたアプリの起動・フォーカス切り替え |
| `wait` | 指定秒数待機（安全監視・キルスイッチ即時割り込み対応） |
| `get_screen_info` | モニター構成・アクティブアプリ・OS権限・オーバーレイ状態を取得 |
| `get_safety_status` | ホワイトリスト設定・一時停止/緊急停止状態を取得 |
| `resume_automation` | 人間優先ロック・キルスイッチ停止状態を手動解除して再開 |

---

## 安全制御（ガードレール）

- **アプリホワイトリスト (`always_allowed_apps` / `blocked_apps`)**:
  `config.json` に許可アプリ・禁止アプリを定義。`blocked_apps`（`1Password`, `Terminal`, `cmd.exe` 等）は常に遮断され、未登録アプリは `unlisted_app_policy`（既定 `"prompt"`）に従って確認ダイアログを表示またはブロックします。
- **緊急停止キルスイッチ**:
  `Esc` キーを短時間（1.2秒以内）に **2回連打**、または緊急ホットキー `Ctrl + Alt + Shift + X` で即座にすべての自動操作を緊急停止します。
- **人間優先ロック (Human-Priority Lock)**:
  AIの操作中に人間が物理マウスを大きく動かしたりキーボードを押した場合、即座に自動操作を一時停止（オーバーレイ表示も `PAUSED` に変化）し、人間へ制御を譲ります。

---

## 単体テスト・動作確認

仮想カーソルが `assets/virtual_cursor.png` を使って画面上を移動・クリックするテストを含む全テストスイートの実行:

```bash
# 全テスト実行（Linuxヘッドレス環境では xvfb-run を併用）
pytest -v

# 仮想カーソルの移動・クリック単体テストを直接実行
python tests/test_cursor_overlay_visual.py

# 画面上で動くデモを再生
python cursor_overlay.py --demo
```
