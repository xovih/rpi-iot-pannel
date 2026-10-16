#!/usr/bin/env bash
# Membuat atau melengkapi config.json Panel IoT CAKRA.
#
#   bash generate-config.sh                         lengkapi config yang ada (hasil wizard)
#   bash generate-config.sh --token gw_live_…       buat config baru
#   bash generate-config.sh --gpio mock             untuk PC tanpa GPIO
#
# Mesin dibaca dari backend; pin counter dari config.example.json.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"
if [ -x "$HERE/.venv/bin/python" ]; then
  exec "$HERE/.venv/bin/python" -m cakra_iot.genconfig "$@"
fi
PYTHONPATH="$HERE/src" exec python3 -m cakra_iot.genconfig "$@"
