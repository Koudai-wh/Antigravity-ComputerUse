#!/bin/bash
# macOS Finder からダブルクリックでそのまま起動できるランチャー
set -e
cd "$(dirname "$0")"

echo "=== Antigravity Computer Use (macOS Launcher) ==="

if ! command -v python3 >/dev/null 2>&1; then
  echo "[エラー] python3 が見つかりません。Homebrew または python.org から Python 3.11+ をインストールしてください。"
  read -r -p "Enterキーで閉じます..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[初回セットアップ] 仮想環境 (.venv) を作成して依存パッケージをインストールします..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

if [[ "${1:-}" == "--classic" ]]; then
  echo "クラシックコントロールパネルを起動します..."
  .venv/bin/python launcher_gui.py
else
  echo "Antigravity AI Studio を起動します..."
  .venv/bin/python studio_app.py
fi
