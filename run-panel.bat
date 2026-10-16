@echo off
rem Menjalankan Panel IoT CAKRA di Windows (pengujian/development) dengan klik-ganda.
rem Jalan pertama membuat .venv dan memasang panel; berikutnya langsung membuka panel.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Menyiapkan .venv pertama kali...
  py -3 -m venv .venv || python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -e .
)
start "" ".venv\Scripts\pythonw.exe" -m cakra_iot --windowed %*
endlocal
