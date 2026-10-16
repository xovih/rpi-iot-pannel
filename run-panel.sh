#!/usr/bin/env bash
# Menjalankan Panel IoT CAKRA — aman diklik-ganda dari file manager.
#
# Jalan pertama membuat virtual environment `.venv` (memakai PyQt5 dari paket
# OS bila ada) lalu memasang panel; jalan berikutnya langsung membuka panel.
# Bila config.json belum ada, panel membuka wizard pengaturan awal.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"
mkdir -p data
# Tanpa terminal, keluaran launcher hanya terbaca dari berkas ini.
exec >>"data/launcher.log" 2>&1
echo "── $(date '+%F %T') menjalankan panel ──"

# Sesi desktop Raspberry Pi kadang tidak mewariskan DISPLAY ke launcher.
export DISPLAY="${DISPLAY:-:0}"

VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Menyiapkan .venv pertama kali…"
  python3 -m venv --system-site-packages "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  EXTRA=""
  if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    EXTRA="[raspberry-pi]"
  fi
  "$VENV/bin/python" -m pip install -e ".${EXTRA}"
fi

exec "$VENV/bin/python" -m cakra_iot "$@"
