#!/usr/bin/env bash
# Uji pulsa counter GPIO tanpa membuka panel.
#
#   bash gpio-test.sh                 semua pin dari config.json
#   bash gpio-test.sh --pins 17,27    pin BCM tertentu
#   bash gpio-test.sh --seconds 60    berhenti otomatis
#
# Tutup panel (tombol ✕) terlebih dahulu: GPIO hanya boleh dipakai satu proses.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"
if [ -x "$HERE/.venv/bin/python" ]; then
  exec "$HERE/.venv/bin/python" -m cakra_iot.gpiotest "$@"
fi
PYTHONPATH="$HERE/src" exec python3 -m cakra_iot.gpiotest "$@"
