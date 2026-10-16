#!/usr/bin/env bash
# Membangun Panel IoT CAKRA menjadi satu berkas executable: dist/cakra-panel.
#
# Jalankan di Raspberry Pi itu sendiri (arsitektur binary mengikuti mesin
# pembangun). Hasilnya dapat diklik-ganda tanpa Python/venv; config.json dan
# folder data/ diletakkan di samping binary.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"

VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv --system-site-packages "$VENV"
fi
EXTRA="build"
if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
  EXTRA="build,raspberry-pi"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -e ".[${EXTRA}]"

HIDDEN=()
if "$VENV/bin/python" -c "import RPi.GPIO" 2>/dev/null; then
  HIDDEN+=(--hidden-import RPi.GPIO)
fi

"$VENV/bin/python" -m PyInstaller \
  --noconfirm --clean --onefile --windowed \
  --name cakra-panel \
  --paths src \
  "${HIDDEN[@]}" \
  src/cakra_iot/__main__.py

# Config lapangan dibawa ke samping binary bila sudah ada.
if [ -f config.json ] && [ ! -f dist/config.json ]; then
  cp config.json dist/config.json
  chmod 600 dist/config.json
fi

echo "Selesai: $HERE/dist/cakra-panel"
echo "Pasang ikon Desktop dengan: ./install-shortcut.sh"
