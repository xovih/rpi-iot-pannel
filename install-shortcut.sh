#!/usr/bin/env bash
# Memasang ikon "CAKRA Panel IoT" di Desktop dan menu aplikasi Raspberry Pi.
#
#   ./install-shortcut.sh             ikon Desktop + menu aplikasi
#   ./install-shortcut.sh --autostart ikut dibuka otomatis setelah login
#
# Ikon menjalankan binary `dist/cakra-panel` bila sudah dibangun dengan
# build-executable.sh, dan `run-panel.sh` bila belum.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
AUTOSTART=0
[ "${1:-}" = "--autostart" ] && AUTOSTART=1

if [ -x "$HERE/dist/cakra-panel" ]; then
  EXEC="$HERE/dist/cakra-panel"
  WORKDIR="$HERE/dist"
else
  chmod +x "$HERE/run-panel.sh"
  EXEC="$HERE/run-panel.sh"
  WORKDIR="$HERE"
fi

ENTRY="[Desktop Entry]
Type=Application
Version=1.0
Name=CAKRA Panel IoT
Comment=Panel kiosk produksi CAKRA (antrean peti, operator, counter mesin)
Exec=\"$EXEC\"
Path=$WORKDIR
Icon=$HERE/deploy/cakra-panel.svg
Terminal=false
Categories=Utility;
StartupNotify=true"

install_entry() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  printf '%s\n' "$ENTRY" >"$target"
  chmod +x "$target"
  # Tandai tepercaya agar klik-ganda tidak bertanya "Execute?" lagi.
  gio set "$target" metadata::trusted true 2>/dev/null || true
  echo "Terpasang: $target"
}

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
install_entry "$DESKTOP_DIR/cakra-panel.desktop"
install_entry "$HOME/.local/share/applications/cakra-panel.desktop"
[ "$AUTOSTART" = 1 ] && install_entry "$HOME/.config/autostart/cakra-panel.desktop"

echo "Selesai. Klik-ganda ikon 'CAKRA Panel IoT' di Desktop untuk membuka panel."
