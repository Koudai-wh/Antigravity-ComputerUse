@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Antigravity Computer Use Launcher

echo === Antigravity Computer Use (Windows / UTM VM Launcher) ===

where py >nul 2>&1
if %errorlevel%==0 (
  set PY_CMD=py -3
) else (
  set PY_CMD=python
)

if not exist ".venv\Scripts\python.exe" (
  echo [初回セットアップ] 仮想環境 (.venv) を作成し、依存パッケージをインストールします...
  %PY_CMD% -m venv .venv
  if errorlevel 1 (
    echo [エラー] Python 3.11+ が見つかりません。https://www.python.org/downloads/ からインストールしてください。
    pause
    exit /b 1
  )
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\pip.exe install -r requirements.txt
)

if "%~1"=="--classic" (
  echo クラシックコントロールパネルを起動します...
  .venv\Scripts\python.exe launcher_gui.py
) else (
  echo Antigravity AI Studio を起動します...
  .venv\Scripts\python.exe studio_app.py
)
if errorlevel 1 pause
