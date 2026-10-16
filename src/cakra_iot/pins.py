"""Sumber pemetaan pin GPIO counter panel (revisi 16 Okt 2026).

Urutan prioritas:

1. **backend** — menu Gateway / Panel IoT → Pin GPIO (sumber kebenaran untuk
   seluruh 28 panel; dapat diubah tanpa mendatangi Raspberry Pi);
2. **cache** — salinan pemetaan backend terakhir di SQLite, agar panel yang
   dinyalakan tanpa jaringan tetap menghitung;
3. **config** — `counters` di `config.json` untuk panel yang pinnya belum
   diisi di backend (kompatibilitas sebelum migrasi).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig, CounterSlot

CACHE_KEY = "gpio_pins"


@dataclass(frozen=True)
class PinPlan:
    source: str
    pull: str
    edge: str
    bounce_ms: int
    counters: dict[str, tuple[CounterSlot, ...]] = field(default_factory=dict)
    version: str | None = None

    def slots_for(self, dal: str) -> tuple[CounterSlot, ...]:
        return self.counters.get(dal.strip().upper(), ())

    @property
    def all_slots(self) -> tuple[CounterSlot, ...]:
        return tuple(slot for slots in self.counters.values() for slot in slots)

    @property
    def signature(self) -> str:
        """Berubah bila ada pin/setelan yang berbeda → GPIO perlu dipasang ulang."""
        return json.dumps(self.to_cache(), sort_keys=True)

    def to_cache(self) -> dict[str, Any]:
        return {
            "pull": self.pull,
            "edge": self.edge,
            "bounce_ms": self.bounce_ms,
            "version": self.version,
            "counters": {
                dal: [{"code": s.code, "pins": list(s.pins), "slot_no": s.slot_no} for s in slots]
                for dal, slots in sorted(self.counters.items())
            },
        }


def from_config(config: AppConfig) -> PinPlan:
    counters = {
        counter.dal: tuple(
            CounterSlot(code=slot.code, pins=slot.pins, slot_no=slot.slot_no or index)
            for index, slot in enumerate(counter.slots, start=1)
        )
        for counter in config.counters
    }
    return PinPlan("config", config.gpio_pull, config.gpio_edge, config.gpio_bounce_ms, counters)


def from_cache(raw: Any) -> PinPlan | None:
    if not isinstance(raw, dict) or not raw.get("counters"):
        return None
    counters = {
        str(dal).upper(): tuple(
            CounterSlot(code=str(s["code"]), pins=tuple(int(p) for p in s["pins"]), slot_no=int(s.get("slot_no") or 0))
            for s in slots
        )
        for dal, slots in raw["counters"].items()
    }
    return PinPlan(
        "cache",
        str(raw.get("pull", "up")),
        str(raw.get("edge", "rising")),
        int(raw.get("bounce_ms", 110)),
        counters,
        raw.get("version"),
    )


def from_bootstrap(bootstrap: dict[str, Any]) -> PinPlan | None:
    """`None` bila panel ini belum punya pemetaan pin di backend."""
    gpio = bootstrap.get("gpio") or {}
    if not gpio.get("managed"):
        return None
    counters: dict[str, tuple[CounterSlot, ...]] = {}
    for machine in bootstrap.get("machines", []):
        dal = str(machine.get("dalCode", "")).strip().upper()
        counters[dal] = tuple(
            CounterSlot(
                code=str(slot["code"]).upper(),
                pins=tuple(int(pin) for pin in slot["pins"]),
                slot_no=int(slot.get("slotNo") or 0),
            )
            for slot in sorted(machine.get("counterSlots", []), key=lambda item: item.get("slotNo", 0))
        )
    return PinPlan(
        "backend",
        str(gpio.get("pull", "up")),
        str(gpio.get("edge", "rising")),
        int(gpio.get("bounceMs", 110)),
        counters,
        gpio.get("version"),
    )
