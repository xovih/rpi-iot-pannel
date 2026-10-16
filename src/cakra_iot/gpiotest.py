"""Uji counter GPIO tanpa GUI — dipakai sebelum panel menghitung produksi.

    python -m cakra_iot.gpiotest                 # pin dari config.json
    python -m cakra_iot.gpiotest --pins 17,27    # pin BCM tertentu saja
    python -m cakra_iot.gpiotest --seconds 60    # berhenti sendiri

Setiap pulsa dicetak beserta pin, counter, total, dan jarak dari pulsa
sebelumnya, sehingga teknisi dapat memastikan satu benda = satu pulsa sebelum
counter sungguhan dipakai. Tidak menulis apa pun ke SQLite panel.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import CAUTION_BCM_PINS, VALID_BCM_PINS, AppConfig, find_config
from .lock import single_instance

DRIVER_HINT = (
    "RPi.GPIO tidak terpasang. Di Raspberry Pi OS jalankan:\n"
    "  sudo apt install -y python3-rpi-lgpio\n"
    "(pengganti RPi.GPIO yang kompatibel dengan kernel Bookworm terbaru)."
)
EDGE_HINT = (
    "Edge detection gagal. RPi.GPIO 0.7.x tidak cocok dengan kernel Bookworm 6.6+.\n"
    "Ganti dengan pustaka kompatibel:\n"
    "  sudo apt remove -y python3-rpi.gpio\n"
    "  sudo apt install -y python3-rpi-lgpio\n"
    "Pastikan juga pengguna termasuk grup gpio: sudo usermod -aG gpio $USER (lalu login ulang)."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uji pulsa counter GPIO Panel IoT CAKRA")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--pins", help="Pin BCM dipisah koma; tanpa ini memakai config.json")
    parser.add_argument("--seconds", type=int, default=0, help="Lama uji; 0 = sampai Ctrl+C")
    parser.add_argument("--bounce-ms", type=int, default=None)
    parser.add_argument("--edge", choices=["falling", "rising", "both"], default=None)
    parser.add_argument("--pull", choices=["up", "down", "off"], default=None)
    args = parser.parse_args(argv)

    config_path = find_config(args.config)
    config = AppConfig.load(config_path) if config_path else None
    pull = args.pull or (config.gpio_pull if config else "up")
    edge = args.edge or (config.gpio_edge if config else "falling")
    bounce = args.bounce_ms or (config.gpio_bounce_ms if config else 110)

    if args.pins:
        mapping = {int(pin): f"GPIO{int(pin)}" for pin in args.pins.split(",") if pin.strip()}
    elif config and config.all_slots:
        mapping = {pin: slot.code for slot in config.all_slots for pin in slot.pins}
    else:
        print("Tidak ada pin untuk diuji. Isi counters di config.json atau pakai --pins 17,27.")
        return 2
    invalid = [pin for pin in mapping if pin not in VALID_BCM_PINS]
    if invalid:
        print(f"Pin {invalid} bukan nomor BCM yang sah (2-27). Gunakan nomor BCM, bukan nomor fisik.")
        return 2

    lock = single_instance(config.database_path.parent / "panel.lock") if config else None
    if config and lock is None:
        print("Panel IoT sedang berjalan dan memakai GPIO. Tutup panel (tombol ✕) lalu ulangi.")
        return 1
    try:
        return run(mapping, pull, edge, bounce, args.seconds, config_path)
    finally:
        if lock is not None:
            lock.close()


def run(
    mapping: dict[int, str],
    pull: str,
    edge: str,
    bounce: int,
    seconds: int,
    config_path: Path | None,
) -> int:
    try:
        import RPi.GPIO as GPIO  # type: ignore[import-not-found]
    except ImportError:
        print(DRIVER_HINT)
        return 1

    origin = getattr(GPIO, "__file__", "?")
    print(f"Driver GPIO: {getattr(GPIO, 'VERSION', '?')} — {origin}")
    if "site-packages" in origin and ".venv" in origin:
        print(
            "Peringatan: driver dimuat dari .venv. Bila edge detection gagal, jalankan:\n"
            "  .venv/bin/python -m pip uninstall -y RPi.GPIO"
        )
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    pud = {"up": GPIO.PUD_UP, "down": GPIO.PUD_DOWN, "off": GPIO.PUD_OFF}[pull]
    trigger = {"falling": GPIO.FALLING, "rising": GPIO.RISING, "both": GPIO.BOTH}[edge]
    idle_expected = 1 if pull == "up" else 0

    print(f"Pull {pull} | edge {edge} | debounce {bounce} ms | config {config_path or '-'}")
    print("PIN BCM  COUNTER          LEVEL AWAL")
    for pin, code in sorted(mapping.items()):
        GPIO.setup(pin, GPIO.IN, pull_up_down=pud)
        level = GPIO.input(pin)
        note = "" if level == idle_expected else "  ← aktif terus? cek sensor/opto/kabel"
        caution = f"  ({CAUTION_BCM_PINS[pin]})" if pin in CAUTION_BCM_PINS else ""
        print(f"GPIO{pin:<5} {code:<16} {level}{note}{caution}")

    counts: dict[int, int] = {pin: 0 for pin in mapping}
    last: dict[int, float] = {}
    guard = threading.Lock()

    def on_pulse(pin: int) -> None:
        now = time.monotonic()
        with guard:
            counts[pin] += 1
            gap = f"{(now - last[pin]) * 1000:7.0f} ms" if pin in last else "      -   "
            last[pin] = now
            total = counts[pin]
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"{stamp}  GPIO{pin:<3} {mapping[pin]:<16} #{total:<6} jarak {gap}", flush=True)

    try:
        for pin in mapping:
            GPIO.add_event_detect(pin, trigger, callback=on_pulse, bouncetime=bounce)
    except RuntimeError as exc:
        print(f"Gagal: {exc}\n{EDGE_HINT}")
        GPIO.cleanup()
        return 1

    print("\nMenunggu pulsa… lewatkan benda di depan sensor. Ctrl+C untuk berhenti.\n")
    started = time.monotonic()
    try:
        while not seconds or time.monotonic() - started < seconds:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
    print("\nRINGKASAN")
    for pin, total in sorted(counts.items()):
        print(f"  GPIO{pin:<4} {mapping[pin]:<16} {total} pulsa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
