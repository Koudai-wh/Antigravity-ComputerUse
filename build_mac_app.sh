#!/usr/bin/env bash
# Build standalone macOS application: dist/Antigravity AI Studio.app
# Includes virtual_cursor.png icon (.icns), TCC Info.plist privacy descriptions,
# Retina High-DPI support, and ad-hoc codesigning so macOS Screen Recording &
# Accessibility permissions register independently for Antigravity AI Studio.app.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[ERROR] build_mac_app.sh must be executed on macOS (Darwin)."
  exit 1
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

APP_NAME="Antigravity AI Studio"
BUNDLE_ID="com.antigravity.aistudio"
ICON_SRC="assets/virtual_cursor.png"
ICONSET_DIR="build/AppIcon.iconset"
ICNS_OUT="assets/app_icon.icns"

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

echo "=== Building ${APP_NAME}.app ==="

if [[ -d ".venv" ]]; then
  if ! .venv/bin/python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
    echo "[1/5] Existing .venv uses Python < 3.10; recreating..."
    rm -rf .venv
  fi
fi

if [[ ! -d ".venv" ]]; then
  echo "[1/5] Creating Python virtual environment (.venv)..."
  if PY_BIN="$(find_python)"; then
    echo "Using Python: ${PY_BIN} ($("${PY_BIN}" --version))"
    "${PY_BIN}" -m venv .venv
  elif command -v uv >/dev/null 2>&1; then
    echo "Installing Python 3.11 via uv..."
    uv python install 3.11
    uv venv --python 3.11 .venv
  elif command -v brew >/dev/null 2>&1; then
    echo "Installing python@3.11 via Homebrew..."
    brew install python@3.11
    PY_BIN="$(find_python)"
    "${PY_BIN}" -m venv .venv
  else
    echo "[ERROR] Python 3.10+ (recommended 3.11+) is required."
    echo "macOS default /usr/bin/python3 is 3.9. Please install Python 3.11+:"
    echo "  brew install python@3.11"
    echo "or download from https://www.python.org/downloads/macos/"
    exit 1
  fi
fi

echo "[2/5] Installing runtime and build dependencies (PyInstaller)..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install "pyinstaller>=6.10.0"

echo "[3/5] Generating macOS .icns icon from ${ICON_SRC}..."
if [[ ! -f "${ICON_SRC}" ]]; then
  echo "[ERROR] Missing ${ICON_SRC}"
  exit 1
fi

rm -rf "${ICONSET_DIR}"
mkdir -p "${ICONSET_DIR}"

sips -s format png -z 16 16     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_16x16.png"      >/dev/null
sips -s format png -z 32 32     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_16x16@2x.png"   >/dev/null
sips -s format png -z 32 32     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_32x32.png"      >/dev/null
sips -s format png -z 64 64     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_32x32@2x.png"   >/dev/null
sips -s format png -z 128 128   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_128x128.png"    >/dev/null
sips -s format png -z 256 256   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
sips -s format png -z 256 256   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_256x256.png"    >/dev/null
sips -s format png -z 512 512   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
sips -s format png -z 512 512   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_512x512.png"    >/dev/null
sips -s format png -z 1024 1024 "${ICON_SRC}" --out "${ICONSET_DIR}/icon_512x512@2x.png" >/dev/null

if ! iconutil -c icns "${ICONSET_DIR}" -o "${ICNS_OUT}"; then
  echo "[WARN] iconutil failed; falling back to Pillow ICNS export..."
  .venv/bin/python -c "from PIL import Image; Image.open('${ICON_SRC}').save('${ICNS_OUT}', format='ICNS')"
fi
rm -rf "${ICONSET_DIR}"

echo "[4/5] Running PyInstaller..."
.venv/bin/pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "${APP_NAME}" \
  --icon "${ICNS_OUT}" \
  --osx-bundle-identifier "${BUNDLE_ID}" \
  --add-data "assets:assets" \
  --add-data "config.json:." \
  --collect-all "mcp" \
  --collect-submodules "uvicorn" \
  --collect-submodules "starlette" \
  --collect-submodules "sse_starlette" \
  --collect-submodules "anyio" \
  --collect-submodules "pynput" \
  --collect-submodules "Quartz" \
  --collect-submodules "AppKit" \
  --hidden-import "mss" \
  --hidden-import "pyautogui" \
  --hidden-import "pyperclip" \
  --hidden-import "ApplicationServices" \
  --hidden-import "artifact_parser" \
  --hidden-import "computer_use_mcp" \
  --hidden-import "config_loader" \
  --hidden-import "cursor_overlay" \
  --hidden-import "gui_controller" \
  --hidden-import "install_mcp_config" \
  --hidden-import "safety" \
  --hidden-import "vlm_client" \
  studio_app.py

APP_BUNDLE="dist/${APP_NAME}.app"
PLIST_PATH="${APP_BUNDLE}/Contents/Info.plist"

echo "[5/5] Updating Info.plist TCC permissions & ad-hoc codesigning..."
/usr/libexec/PlistBuddy -c "Delete :NSHighResolutionCapable" "${PLIST_PATH}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "${PLIST_PATH}"

/usr/libexec/PlistBuddy -c "Delete :NSScreenCaptureUsageDescription" "${PLIST_PATH}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSScreenCaptureUsageDescription string 'Antigravity AI Studio captures your display or Screen Sharing window for local AI Computer Use and PiP Live Preview.'" "${PLIST_PATH}"

/usr/libexec/PlistBuddy -c "Delete :NSAppleEventsUsageDescription" "${PLIST_PATH}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string 'Antigravity AI Studio uses AppleEvents to focus allowed apps and paste Unicode/Japanese text cleanly.'" "${PLIST_PATH}"

xattr -cr "${APP_BUNDLE}" 2>/dev/null || true
codesign --force --deep --sign - "${APP_BUNDLE}"

echo ""
echo "✅ Build complete: ${APP_BUNDLE}"
echo "   - Double-click in Finder or copy to /Applications:"
echo "     cp -R \"${APP_BUNDLE}\" /Applications/"
echo "   - Grant Screen Recording & Accessibility in System Settings -> Privacy & Security."
