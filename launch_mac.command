#!/bin/bash
# macOS Finder からダブルクリックでそのまま起動できるランチャー
set -euo pipefail
cd "$(dirname "$0")"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

on_error() {
  echo ""
  echo "[エラー] 起動またはセットアップに失敗しました。上のメッセージを確認してください。"
  read -r -p "Enterキーで閉じます..."
}
trap on_error ERR

find_python() {
  local candidates=(
    "python3.13"
    "python3.12"
    "python3.11"
    "python3.10"
    "/opt/homebrew/bin/python3.13"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3.13"
    "/usr/local/bin/python3.12"
    "/usr/local/bin/python3.11"
    "/usr/local/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
    "python3"
  )
  for py in "${candidates[@]}"; do
    if command -v "$py" >/dev/null 2>&1; then
      if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
        command -v "$py"
        return 0
      fi
    fi
  done
  return 1
}

echo "=== Antigravity AI Studio (macOS Launcher) ==="

if [ -d ".venv" ]; then
  if ! .venv/bin/python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
    echo "[再構築] 古いPythonバージョンの .venv を削除して再作成します..."
    rm -rf .venv
  fi
fi

if [ ! -d ".venv" ]; then
  echo "[初回セットアップ] 仮想環境 (.venv) を作成して依存パッケージをインストールします..."
  if PY_BIN="$(find_python)"; then
    echo "使用Python: $PY_BIN ($("$PY_BIN" --version))"
    "$PY_BIN" -m venv .venv
  elif command -v uv >/dev/null 2>&1; then
    uv python install 3.11
    uv venv --seed --python 3.11 .venv
  elif command -v brew >/dev/null 2>&1; then
    brew install python@3.11
    PY_BIN="$(find_python)"
    "$PY_BIN" -m venv .venv
  else
    echo "[エラー] Python 3.10以上（推奨 3.11+）が見つかりません。"
    echo "macOS 標準の python3 (3.9) では mcp パッケージが動作しないため、以下を実行してください:"
    echo "  brew install python@3.11"
    echo "または https://www.python.org/downloads/macos/ からインストールしてください。"
    exit 1
  fi
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

if ! .venv/bin/python -c "import PySide6, mcp, openai" >/dev/null 2>&1; then
  echo "[セットアップ] 依存パッケージをインストールしています..."
  if .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    echo "[エラー] .venv 内に pip が見つかりません。rm -rf .venv してから再実行してください。"
    exit 1
  fi
fi

if [[ "${1:-}" == "--classic" ]]; then
  echo "クラシックコントロールパネルを起動します..."
  .venv/bin/python launcher_gui.py
else
  echo "Antigravity AI Studio を起動します..."
  .venv/bin/python studio_app.py
fi
