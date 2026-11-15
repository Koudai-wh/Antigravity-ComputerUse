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

APP_NAME="Antigravity AI Studio"
BUNDLE_ID="com.antigravity.aistudio"
ICON_SRC="assets/virtual_cursor.png"
ICONSET_DIR="build/AppIcon.iconset"
ICNS_OUT="assets/app_icon.icns"

echo "=== Building ${APP_NAME}.app ==="

if [[ ! -d ".venv" ]]; then
  echo "[1/5] Creating Python virtual environment (.venv)..."
  python3 -m venv .venv
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

sips -z 16 16     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_16x16.png"      >/dev/null
sips -z 32 32     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_16x16@2x.png"   >/dev/null
sips -z 32 32     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_32x32.png"      >/dev/null
sips -z 64 64     "${ICON_SRC}" --out "${ICONSET_DIR}/icon_32x32@2x.png"   >/dev/null
sips -z 128 128   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_128x128.png"    >/dev/null
sips -z 256 256   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_256x256.png"    >/dev/null
sips -z 512 512   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "${ICON_SRC}" --out "${ICONSET_DIR}/icon_512x512.png"    >/dev/null
cp "${ICON_SRC}"  "${ICONSET_DIR}/icon_512x512@2x.png"

iconutil -c icns "${ICONSET_DIR}" -o "${ICNS_OUT}"
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
  --hidden-import "mcp" \
  --hidden-import "mcp.server.mcpserver" \
  --hidden-import "mss" \
  --hidden-import "pynput" \
  --hidden-import "pynput.keyboard._darwin" \
  --hidden-import "pynput.mouse._darwin" \
  --hidden-import "pyautogui" \
  --hidden-import "pyperclip" \
  --hidden-import "Quartz" \
  --hidden-import "AppKit" \
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

codesign --force --deep --sign - "${APP_BUNDLE}"

echo ""
echo "✅ Build complete: ${APP_BUNDLE}"
echo "   - Double-click in Finder or copy to /Applications:"
echo "     cp -R \"${APP_BUNDLE}\" /Applications/"
echo "   - Grant Screen Recording & Accessibility in System Settings -> Privacy & Security."
