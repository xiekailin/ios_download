#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="XDownloaderMac.app"
APP_DEST="/Applications/${APP_NAME}"
PYINSTALLER_VENV="/tmp/xdl-pyinstaller-venv"
PYTHON="${PYINSTALLER_VENV}/bin/python"
PYINSTALLER="${PYINSTALLER_VENV}/bin/pyinstaller"
HEALTH_URL="http://127.0.0.1:18767/api/v1/health"

echo "Building Mac app..."
xcodebuild -quiet \
  -project "${ROOT_DIR}/client/XDownloader.xcodeproj" \
  -scheme XDownloaderMac \
  -configuration Debug \
  build

BUILT_PRODUCTS_DIR="$(xcodebuild -showBuildSettings \
  -project "${ROOT_DIR}/client/XDownloader.xcodeproj" \
  -scheme XDownloaderMac \
  -configuration Debug 2>/dev/null \
  | awk -F'= ' '/BUILT_PRODUCTS_DIR =/ {print $2; exit}')"

if [[ ! -d "${BUILT_PRODUCTS_DIR}/${APP_NAME}" ]]; then
  echo "Built app not found: ${BUILT_PRODUCTS_DIR}/${APP_NAME}" >&2
  exit 1
fi

if [[ ! -x "${PYINSTALLER}" ]]; then
  echo "Preparing PyInstaller venv..."
  rm -rf "${PYINSTALLER_VENV}"
  python3 -m venv "${PYINSTALLER_VENV}"
  "${PYTHON}" -m pip install -U pip pyinstaller
  "${PYTHON}" -m pip install "fastapi>=0.135.1,<1.0.0" "pydantic>=2.12.5,<3.0.0" "uvicorn>=0.42.0,<1.0.0" "httpx>=0.28.1,<1.0.0" "python-multipart>=0.0.20,<1.0.0"
fi

if [[ ! -x "${PYINSTALLER}" ]]; then
  echo "PyInstaller executable not found: ${PYINSTALLER}" >&2
  exit 1
fi

"${PYTHON}" -m pip install "fastapi>=0.135.1,<1.0.0" "pydantic>=2.12.5,<3.0.0" "uvicorn>=0.42.0,<1.0.0" "httpx>=0.28.1,<1.0.0" "python-multipart>=0.0.20,<1.0.0"

echo "Building backend onedir..."
(cd "${ROOT_DIR}" && "${PYINSTALLER}" --clean -y xdownloader-backend.spec)

if [[ ! -x "${ROOT_DIR}/dist/xdownloader-backend/xdownloader-backend" ]]; then
  echo "Backend executable not found: ${ROOT_DIR}/dist/xdownloader-backend/xdownloader-backend" >&2
  exit 1
fi

echo "Stopping old app/backend..."
pids="$(pgrep -f '/Applications/XDownloaderMac.app/Contents/MacOS/XDownloaderMac|/Applications/XDownloaderMac.app/Contents/Resources/backend/xdownloader-backend/xdownloader-backend' || true)"
if [[ -n "${pids}" ]]; then
  # shellcheck disable=SC2086
  kill ${pids} || true
  for _ in {1..20}; do
    running=""
    for pid in ${pids}; do
      if kill -0 "${pid}" 2>/dev/null; then
        running="${running} ${pid}"
      fi
    done
    [[ -z "${running}" ]] && break
    sleep 0.1
  done
  if [[ -n "${running:-}" ]]; then
    # shellcheck disable=SC2086
    kill -9 ${running} || true
  fi
fi

echo "Installing app to ${APP_DEST}..."
rm -rf "${APP_DEST}"
cp -R "${BUILT_PRODUCTS_DIR}/${APP_NAME}" "/Applications/"
rm -rf "${APP_DEST}/Contents/Resources/backend"
mkdir -p "${APP_DEST}/Contents/Resources/backend"
cp -R "${ROOT_DIR}/dist/xdownloader-backend" "${APP_DEST}/Contents/Resources/backend/"

if [[ ! -x "${APP_DEST}/Contents/Resources/backend/xdownloader-backend/xdownloader-backend" ]]; then
  echo "Installed backend executable missing" >&2
  exit 1
fi

echo "Signing installed app..."
codesign --force --deep --sign - "${APP_DEST}"
codesign --verify --deep --strict "${APP_DEST}"

echo "Opening app..."
open "${APP_DEST}"

echo "Waiting for backend health..."
for _ in {1..80}; do
  if curl --noproxy '*' -fsS --max-time 1 "${HEALTH_URL}" | grep -q '"status":"ok"'; then
    echo "Backend healthy: ${HEALTH_URL}"
    exit 0
  fi
  sleep 0.25
done

echo "Backend did not become healthy in time" >&2
exit 1
