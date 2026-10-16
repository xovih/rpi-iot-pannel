from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.json"

# Pin BCM yang dapat dipakai sebagai input counter pada header 40-pin Pi 4.
VALID_BCM_PINS = range(2, 28)
# Pin yang punya fungsi khusus: 2/3 memiliki pull-up eksternal 1,8 kΩ (I2C),
# 14/15 dipakai UART konsol. Boleh dipakai, tetapi diberi peringatan.
CAUTION_BCM_PINS = {2: "I2C SDA", 3: "I2C SCL", 14: "UART TX", 15: "UART RX"}


@dataclass(frozen=True)
class CounterSlot:
    code: str
    pins: tuple[int, ...]
    # 1 = Spur 1, 2 = Spur 2; 0 = belum diketahui (diisi urutan config).
    slot_no: int = 0


@dataclass(frozen=True)
class CounterConfig:
    """Pemetaan pin GPIO untuk satu mesin, dikenali dari kode DAL-nya.

    Daftar mesin, nama, dan work center **tidak** lagi ditulis di Raspberry Pi
    (revisi 16 Okt 2026): semuanya dibaca dari backend saat bootstrap, sesuai
    mesin yang dinaungi gateway pada menu Gateway / Panel IoT. Berkas lokal
    hanya menyimpan hal yang memang fisik — kabel sensor ke pin mana.
    """

    dal: str
    slots: tuple[CounterSlot, ...]


@dataclass(frozen=True)
class AppConfig:
    api_base_url: str
    device_token: str
    gateway_code: str
    gpio_mode: str
    telemetry_hz: int
    heartbeat_seconds: int
    idle_after_seconds: int
    fullscreen: bool
    database_path: Path
    recovery_csv_path: Path
    counters: tuple[CounterConfig, ...]
    source: Path | None = None
    # Rangkaian baku: proximity 24 V → optocoupler → kolektor menarik pin ke
    # GND selama produk di depan sensor. Pull-up internal + tepi naik: satu
    # pulsa dihitung saat produk lepas dari sensor (revisi 16 Okt 2026).
    gpio_pull: str = "up"
    gpio_edge: str = "rising"
    gpio_bounce_ms: int = 110
    # Hash PBKDF2 password tombol PENGATURAN (lihat panel_password.py); kosong =
    # device token dipakai sebagai password sementara.
    settings_password_hash: str = ""

    def counter_for(self, dal: str) -> CounterConfig | None:
        key = dal.strip().upper()
        return next((counter for counter in self.counters if counter.dal == key), None)

    @property
    def all_slots(self) -> tuple[CounterSlot, ...]:
        return tuple(slot for counter in self.counters for slot in counter.slots)

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        path = path.resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw, base_dir=path.parent, source=path)

    @classmethod
    def from_dict(
        cls, raw: dict[str, Any], base_dir: Path, source: Path | None = None
    ) -> "AppConfig":
        # `machines` adalah nama lama (≤ 15 Okt 2026); tetap dibaca agar
        # config.json di lapangan tidak wajib ditulis ulang.
        entries = raw.get("counters", raw.get("machines", [])) or []
        counters = tuple(
            CounterConfig(
                dal=str(item["dal"]).strip().upper(),
                slots=tuple(
                    CounterSlot(
                        code=str(slot["code"]).strip().upper(),
                        pins=tuple(int(pin) for pin in (slot.get("pins") or [slot["pin"]])),
                    )
                    for slot in item.get("slots", [])
                ),
            )
            for item in entries
        )
        dals = [counter.dal for counter in counters]
        if len(dals) != len(set(dals)):
            raise ValueError("Satu kode DAL hanya boleh ditulis sekali pada daftar counter.")
        slots = [slot for counter in counters for slot in counter.slots]
        if any(not slot.pins for slot in slots):
            raise ValueError("Setiap logical counter wajib memiliki minimal satu pin GPIO.")
        codes = [slot.code for slot in slots]
        if len(codes) != len(set(codes)):
            raise ValueError("Kode logical counter tidak boleh kembar.")
        pins = [pin for slot in slots for pin in slot.pins]
        invalid = sorted({pin for pin in pins if pin not in VALID_BCM_PINS})
        if invalid:
            raise ValueError(
                f"Pin {', '.join(map(str, invalid))} bukan nomor BCM GPIO yang sah (2-27). "
                "Gunakan nomor BCM, bukan nomor fisik header."
            )
        if len(pins) != len(set(pins)):
            raise ValueError("Satu pin GPIO tidak boleh dipakai oleh lebih dari satu logical counter.")
        if not str(raw.get("api_base_url", "")).strip():
            raise ValueError("api_base_url wajib diisi.")
        if not str(raw.get("device_token", "")).strip():
            raise ValueError("device_token wajib diisi.")

        def local(value: str) -> Path:
            candidate = Path(value)
            return candidate if candidate.is_absolute() else base_dir / candidate

        return cls(
            api_base_url=str(raw["api_base_url"]).strip().rstrip("/"),
            device_token=str(raw["device_token"]).strip(),
            gateway_code=str(raw.get("gateway_code", "")).strip(),
            gpio_mode=str(raw.get("gpio_mode", "mock")),
            telemetry_hz=max(1, int(raw.get("telemetry_hz", 1))),
            heartbeat_seconds=max(10, int(raw.get("heartbeat_seconds", 30))),
            idle_after_seconds=max(3, int(raw.get("idle_after_seconds", 15))),
            fullscreen=bool(raw.get("fullscreen", True)),
            database_path=local(str(raw.get("database_path", "data/panel.db"))),
            recovery_csv_path=local(str(raw.get("recovery_csv_path", "data/recovery.csv"))),
            counters=counters,
            source=source,
            gpio_pull=choice(raw, "gpio_pull", ("up", "down", "off"), "up"),
            gpio_edge=choice(raw, "gpio_edge", ("falling", "rising", "both"), "rising"),
            gpio_bounce_ms=min(1000, max(1, int(raw.get("gpio_bounce_ms", 110)))),
            settings_password_hash=str(raw.get("settings_password_hash", "")).strip(),
        )


def choice(raw: dict[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = str(raw.get(key, default)).strip().lower()
    if value not in allowed:
        raise ValueError(f"{key} harus salah satu dari: {', '.join(allowed)}.")
    return value


def app_dir() -> Path:
    """Folder aplikasi: folder executable bila dibekukan PyInstaller, atau
    akar proyek `cakra-iot-gateway` saat dijalankan dari sumber."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def config_candidates(explicit: Path | None = None) -> list[Path]:
    """Urutan pencarian config.json — dipakai saat panel dibuka dengan
    klik-ganda, ketika folder kerja proses tidak dapat diandalkan."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env = os.environ.get("CAKRA_PANEL_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(app_dir() / CONFIG_FILENAME)
    candidates.append(Path.home() / ".config" / "cakra-panel" / CONFIG_FILENAME)
    candidates.append(Path("/etc/cakra/panel.json"))
    return candidates


def find_config(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.exists() else None
    return next((path for path in config_candidates() if path.exists()), None)


def write_config(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        # Device token adalah kredensial gateway; hanya pemilik yang membaca.
        path.chmod(0o600)
    except OSError:
        pass


def template_counters() -> list[dict[str, Any]]:
    """Pemetaan pin baku dari `config.example.json` di folder aplikasi —
    pin sensor panel `.14` yang sudah diuji di lapangan."""
    example = app_dir() / "config.example.json"
    try:
        raw = json.loads(example.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(raw.get("counters", raw.get("machines", [])) or [])


def merge_counters(
    dals: list[str], existing: list[dict[str, Any]], template: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Satu entri counter per DAL mesin backend. Pin yang sudah diisi teknisi
    dipertahankan; DAL yang belum berpin diambil dari templat; sisanya
    dibiarkan kosong agar teknisi tahu mesin mana yang belum dikabel."""

    def by_dal(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(item.get("dal", "")).strip().upper(): item for item in entries}

    current, defaults = by_dal(existing), by_dal(template)
    merged: list[dict[str, Any]] = []
    used_pins: set[int] = set()
    for dal in dict.fromkeys(item.strip().upper() for item in dals):
        source = current.get(dal) if current.get(dal, {}).get("slots") else defaults.get(dal)
        slots = [
            {"code": str(slot["code"]).upper(), "pins": [int(pin) for pin in (slot.get("pins") or [slot["pin"]])]}
            for slot in (source or {}).get("slots", [])
        ]
        pins = {pin for slot in slots for pin in slot["pins"]}
        if pins & used_pins:
            slots, pins = [], set()  # Hindari pin ganda; teknisi mengisi manual.
        used_pins |= pins
        merged.append({"dal": dal, "slots": slots})
    return merged
