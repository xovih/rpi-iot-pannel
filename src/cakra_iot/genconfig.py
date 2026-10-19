"""Membuat atau melengkapi config.json Panel IoT tanpa GUI.

    python -m cakra_iot.genconfig                       # lengkapi config yang ada
    python -m cakra_iot.genconfig --token gw_live_…     # buat baru
    python -m cakra_iot.genconfig --gpio mock --force   # timpa pin dengan templat
    python -m cakra_iot.genconfig --pin K.1=17 --pin K.2:SPUR1=5 --pin K.2:SPUR2=6
    python -m cakra_iot.genconfig --settings-password 'rahasia'   # password PENGATURAN

Nilai yang tidak diberikan diambil dari config.json yang sudah ada (mis. hasil
wizard), lalu dari config.example.json. Daftar mesin dibaca dari backend lewat
device token; pin counter diambil dari templat untuk DAL yang dikenal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .api import ApiClient, ApiError
from .panel_password import MIN_LENGTH, check_new_password, hash_password
from .config import (
    CAUTION_BCM_PINS,
    CONFIG_FILENAME,
    AppConfig,
    app_dir,
    find_config,
    merge_counters,
    template_counters,
    write_config,
)

DEFAULTS: dict[str, Any] = {
    "api_base_url": "http://192.168.196.196:4000",
    "gpio_mode": "real",
    "telemetry_hz": 1,
    "heartbeat_seconds": 30,
    "idle_after_seconds": 180,
    "fullscreen": True,
    "database_path": "data/panel.db",
    "recovery_csv_path": "data/recovery.csv",
    "gpio_pull": "up",
    "gpio_edge": "rising",
    "gpio_bounce_ms": 110,
}


def parse_pin(spec: str) -> tuple[str, str, list[int]]:
    """`DAL=17,18` (satu counter berkode DAL) atau `DAL:SLOT=5` (counter
    bernama `DAL-SLOT`, mis. Spur). Nomor pin selalu BCM."""
    if "=" not in spec:
        raise ValueError(f"--pin {spec!r} harus berbentuk DAL=PIN atau DAL:SLOT=PIN")
    target, pins_text = spec.split("=", 1)
    dal, _, slot = target.partition(":")
    dal = dal.strip().upper()
    code = f"{dal}-{slot.strip().upper()}" if slot.strip() else dal
    pins = [int(pin) for pin in pins_text.replace(" ", "").split(",") if pin]
    if not dal or not pins:
        raise ValueError(f"--pin {spec!r} belum menyebut DAL atau pin")
    return dal, code, pins


def apply_pins(counters: list[dict[str, Any]], specs: list[str]) -> list[dict[str, Any]]:
    """Pin dari baris perintah menggantikan seluruh slot DAL yang disebut."""
    if not specs:
        return counters
    overrides: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        dal, code, pins = parse_pin(spec)
        overrides.setdefault(dal, []).append({"code": code, "pins": pins})
    result = [
        {"dal": item["dal"], "slots": overrides.pop(str(item["dal"]).upper(), item["slots"])}
        for item in counters
    ]
    # DAL yang belum dinaungi gateway tetap ditulis; panel mengabaikannya
    # sampai mesinnya dipilih pada menu Gateway / Panel IoT.
    result.extend({"dal": dal, "slots": slots} for dal, slots in overrides.items())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buat/lengkapi config.json Panel IoT CAKRA")
    parser.add_argument("--config", type=Path, default=None, help="Lokasi config.json tujuan")
    parser.add_argument("--api", help="Alamat backend, mis. http://192.168.196.196:4000")
    parser.add_argument("--token", help="Device token dari menu Gateway / Panel IoT")
    parser.add_argument("--gateway", help="Kode gateway, mis. RPI-INT-01-TEST")
    parser.add_argument("--gpio", choices=["real", "mock"], help="Mode GPIO")
    parser.add_argument("--force", action="store_true", help="Timpa pin yang sudah ada dengan templat")
    parser.add_argument(
        "--pin",
        action="append",
        default=[],
        metavar="DAL[:SLOT]=PIN[,PIN]",
        help="Pin BCM counter, mis. --pin K.1=17 atau --pin K.2:SPUR1=5 (boleh berulang)",
    )
    parser.add_argument("--bounce-ms", type=int, help="Debounce GPIO dalam milidetik (baku 110)")
    parser.add_argument(
        "--settings-password",
        help=f"Password tombol PENGATURAN di panel (min. {MIN_LENGTH} karakter); disimpan sebagai hash",
    )
    args = parser.parse_args(argv)

    target = args.config or find_config() or app_dir() / CONFIG_FILENAME
    raw: dict[str, Any] = dict(DEFAULTS)
    if target.exists():
        raw.update(json.loads(target.read_text(encoding="utf-8")))
        raw.setdefault("counters", raw.pop("machines", []))
    for key, value in (
        ("api_base_url", args.api),
        ("device_token", args.token),
        ("gateway_code", args.gateway),
        ("gpio_mode", args.gpio),
    ):
        if value:
            raw[key] = value.strip()
    raw["api_base_url"] = str(raw["api_base_url"]).rstrip("/")
    if not raw.get("device_token"):
        print("Device token belum ada. Jalankan dengan --token gw_live_… (menu Gateway / Panel IoT).")
        return 2

    template = template_counters()
    existing = [] if args.force else list(raw.get("counters") or [])
    try:
        bootstrap = ApiClient(raw["api_base_url"], raw["device_token"]).bootstrap()
    except ApiError as exc:
        print(f"Peringatan: backend tidak memvalidasi token ({exc}). Daftar mesin dari config lama.")
        # Tanpa backend, daftar mesin hanya dari config yang ada — templat
        # panel .14 tidak boleh ikut masuk ke panel baru bermesin berbeda.
        dals = [str(item.get("dal")) for item in existing]
    else:
        raw["gateway_code"] = raw.get("gateway_code") or str(bootstrap.get("code", ""))
        if raw["gateway_code"].upper() != str(bootstrap.get("code", "")).upper():
            print(
                f"Peringatan: token milik gateway {bootstrap.get('code')}, bukan "
                f"{raw['gateway_code']}. gateway_code disesuaikan."
            )
            raw["gateway_code"] = str(bootstrap.get("code", ""))
        dals = [machine["dalCode"] for machine in bootstrap.get("machines", [])]
        if (bootstrap.get("gpio") or {}).get("managed"):
            print(
                "Info: pin panel ini dikelola di web (Gateway / Panel IoT → Pin GPIO). "
                "Pin di config.json hanya cadangan saat backend belum mengisi pin."
            )
    raw["counters"] = apply_pins(merge_counters(dals, existing, template), args.pin)
    if args.bounce_ms:
        raw["gpio_bounce_ms"] = args.bounce_ms
    if args.settings_password is not None:
        problem = check_new_password(args.settings_password, args.settings_password)
        if problem:
            print(f"Gagal: {problem}")
            return 2
        raw["settings_password_hash"] = hash_password(args.settings_password)

    # Validasi sebelum menulis: config rusak tidak boleh menimpa yang lama.
    AppConfig.from_dict(raw, base_dir=target.parent)
    write_config(target, raw)
    print(f"config.json ditulis: {target}")
    print(
        f"Gateway {raw.get('gateway_code') or '-'} | GPIO {raw['gpio_mode']} | "
        f"pull {raw['gpio_pull']}, edge {raw['gpio_edge']}, debounce {raw['gpio_bounce_ms']} ms"
    )
    for counter in raw["counters"]:
        pins = ", ".join(
            f"{slot['code']}={'/'.join(str(pin) for pin in slot['pins'])}" for slot in counter["slots"]
        )
        print(f"  {counter['dal']:<8} {pins or 'BELUM ADA PIN — isi dengan --pin'}")
    if not raw.get("settings_password_hash"):
        print(
            "Info: password PENGATURAN belum dibuat; sementara device token dipakai sebagai "
            "password. Buat dengan --settings-password atau dari tombol PENGATURAN di panel."
        )
    caution = sorted(
        {
            pin
            for counter in raw["counters"]
            for slot in counter["slots"]
            for pin in slot["pins"]
            if pin in CAUTION_BCM_PINS
        }
    )
    for pin in caution:
        print(f"Peringatan: GPIO{pin} adalah {CAUTION_BCM_PINS[pin]}; pastikan fungsi itu tidak dipakai.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"Gagal: {exc}")
        sys.exit(2)
