from __future__ import annotations

import unittest
from pathlib import Path

from cakra_iot.config import AppConfig
from cakra_iot.pins import from_bootstrap, from_cache, from_config

BOOTSTRAP = {
    "gpio": {"managed": True, "pull": "up", "edge": "falling", "bounceMs": 30, "version": "2026-10-16T06:00:00Z"},
    "machines": [
        {"dalCode": "I.1", "counterSlots": [{"slotNo": 1, "code": "I.1-SPUR1", "pins": [17, 18]}]},
        {"dalCode": "I.8-1", "counterSlots": [{"slotNo": 2, "code": "I.8-1-SPUR2", "pins": [6]}]},
        {"dalCode": "I.9-1", "counterSlots": []},
    ],
}


class PinPlanTest(unittest.TestCase):
    def test_unmanaged_backend_returns_none(self) -> None:
        self.assertIsNone(from_bootstrap({"gpio": {"managed": False}, "machines": []}))
        self.assertIsNone(from_bootstrap({"machines": []}))  # backend lama

    def test_backend_plan_keeps_spur_numbers(self) -> None:
        plan = from_bootstrap(BOOTSTRAP)
        assert plan is not None
        self.assertEqual(plan.source, "backend")
        self.assertEqual(plan.bounce_ms, 30)
        self.assertEqual(plan.slots_for("i.1")[0].pins, (17, 18))
        self.assertEqual(plan.slots_for("I.8-1")[0].slot_no, 2)
        self.assertEqual(plan.slots_for("I.9-1"), ())

    def test_cache_round_trip_is_identical(self) -> None:
        plan = from_bootstrap(BOOTSTRAP)
        assert plan is not None
        cached = from_cache(plan.to_cache())
        assert cached is not None
        self.assertEqual(cached.source, "cache")
        self.assertEqual(cached.signature, plan.signature)
        self.assertIsNone(from_cache(None))

    def test_config_fallback_numbers_spurs_by_order(self) -> None:
        config = AppConfig.load(Path(__file__).parents[1] / "config.example.json")
        plan = from_config(config)
        self.assertEqual([slot.slot_no for slot in plan.slots_for("I.8-1")], [1, 2])


if __name__ == "__main__":
    unittest.main()
