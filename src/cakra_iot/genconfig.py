"""Membuat atau melengkapi config.json Panel IoT tanpa GUI.

    python -m cakra_iot.genconfig                       # lengkapi config yang ada
    python -m cakra_iot.genconfig --token gw_live_…     # buat baru
    python -m cakra_iot.genconfig --gpio mock --force   # timpa pin dengan templat

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
from .config import (
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
    "idle_after_seconds": 15,
    "fullscreen": True,
    "database_path": "data/panel.db",
    "recovery_csv_path": "data/recovery.csv",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buat/lengkapi config.json Panel IoT CAKRA")
    parser.add_argument("--config", type=Path, default=None, help="Lokasi config.json tujuan")
    parser.add_argument("--api", help="Alamat backend, mis. http://192.168.196.196:4000")
    parser.add_argument("--token", help="Device token dari menu Gateway / Panel IoT")
    parser.add_argument("--gateway", help="Kode gateway, mis. RPI-INT-01-TEST")
    parser.add_argument("--gpio", choices=["real", "mock"], help="Mode GPIO")
    parser.add_argument("--force", action="store_true", help="Timpa pin yang sudah ada dengan templat")
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
        print(f"Peringatan: backend tidak memvalidasi token ({exc}). Pin diambil dari templat.")
        dals = [str(item.get("dal")) for item in (existing or template)]
    else:
        raw["gateway_code"] = raw.get("gateway_code") or str(bootstrap.get("code", ""))
        if raw["gateway_code"].upper() != str(bootstrap.get("code", "")).upper():
            print(
                f"Peringatan: token milik gateway {bootstrap.get('code')}, bukan "
                f"{raw['gateway_code']}. gateway_code disesuaikan."
            )
            raw["gateway_code"] = str(bootstrap.get("code", ""))
        dals = [machine["dalCode"] for machine in bootstrap.get("machines", [])]
    raw["counters"] = merge_counters(dals, existing, template)

    write_config(target, raw)
    AppConfig.load(target)  # memastikan hasilnya valid
    print(f"config.json ditulis: {target}")
    print(f"Gateway {raw.get('gateway_code') or '-'} | GPIO {raw['gpio_mode']}")
    for counter in raw["counters"]:
        pins = ", ".join(
            f"{slot['code']}={'/'.join(str(pin) for pin in slot['pins'])}" for slot in counter["slots"]
        )
        print(f"  {counter['dal']:<8} {pins or 'BELUM ADA PIN — isi manual'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
