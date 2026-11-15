#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

if [ "${1:-}" = "--classic" ]; then
  .venv/bin/python launcher_gui.py
else
  .venv/bin/python studio_app.py
fi
