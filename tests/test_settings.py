from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cakra_iot.config import AppConfig
from cakra_iot.panel_password import (
    LOCKOUT_SECONDS,
    MAX_ATTEMPTS,
    Gatekeeper,
    check_new_password,
    hash_password,
    verify_password,
)
from cakra_iot.settings_dialog import apply_settings

RAW = {
    "api_base_url": "http://10.0.0.1:4000",
    "device_token": "gw_live_lama",
    "gateway_code": "RPI-A",
    "gpio_mode": "real",
    "gpio_bounce_ms": 80,
    "counters": [{"dal": "I.1", "slots": [{"code": "I.1", "pins": [17]}]}],
}


class PasswordTest(unittest.TestCase):
    def test_hash_roundtrip_and_salted(self) -> None:
        first, second = hash_password("rahasia1", 1000), hash_password("rahasia1", 1000)
        self.assertNotEqual(first, second)
        self.assertNotIn("rahasia1", first)
        self.assertTrue(verify_password(first, "rahasia1"))
        self.assertFalse(verify_password(first, "rahasia2"))
        self.assertFalse(verify_password("rusak", "rahasia1"))

    def test_new_password_rules(self) -> None:
        self.assertIsNotNone(check_new_password("abc", "abc"))
        self.assertIsNotNone(check_new_password("rahasia1", "rahasia2"))
        self.assertIsNone(check_new_password("rahasia1", "rahasia1"))

    def test_device_token_is_temporary_password(self) -> None:
        gate = Gatekeeper()
        self.assertTrue(gate.check("", "gw_live_lama", "gw_live_lama"))
        self.assertFalse(gate.check("", "gw_live_lama", "salah"))
        # Setelah password dibuat, token tidak lagi diterima.
        stored = hash_password("rahasia1", 1000)
        self.assertFalse(gate.check(stored, "gw_live_lama", "gw_live_lama"))
        self.assertTrue(gate.check(stored, "gw_live_lama", "rahasia1"))

    def test_lockout_after_repeated_failures(self) -> None:
        now = [100.0]
        gate = Gatekeeper(clock=lambda: now[0])
        stored = hash_password("rahasia1", 1000)
        for _ in range(MAX_ATTEMPTS):
            self.assertFalse(gate.check(stored, "", "salah"))
        self.assertEqual(gate.locked_seconds(), LOCKOUT_SECONDS)
        self.assertFalse(gate.check(stored, "", "rahasia1"))
        now[0] += LOCKOUT_SECONDS
        self.assertTrue(gate.check(stored, "", "rahasia1"))


class ApplySettingsTest(unittest.TestCase):
    def test_keeps_token_and_pins_when_blank(self) -> None:
        updated = apply_settings(
            RAW,
            api_base_url=" http://10.0.0.2:4000/ ",
            gateway_code="rpi-b",
            device_token="  ",
            gpio_mode="mock",
            new_password="",
        )
        self.assertEqual(updated["api_base_url"], "http://10.0.0.2:4000")
        self.assertEqual(updated["gateway_code"], "RPI-B")
        self.assertEqual(updated["device_token"], "gw_live_lama")
        self.assertEqual(updated["gpio_mode"], "mock")
        self.assertEqual(updated["counters"], RAW["counters"])
        self.assertEqual(updated["gpio_bounce_ms"], 80)
        self.assertNotIn("settings_password_hash", updated)
        self.assertEqual(RAW["api_base_url"], "http://10.0.0.1:4000")

    def test_new_token_and_password_loaded_by_config(self) -> None:
        updated = apply_settings(
            RAW,
            api_base_url="http://10.0.0.1:4000",
            gateway_code="RPI-A",
            device_token="gw_live_baru",
            gpio_mode="real",
            new_password="rahasia1",
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig.from_dict(updated, base_dir=Path(directory))
        self.assertEqual(config.device_token, "gw_live_baru")
        self.assertTrue(verify_password(config.settings_password_hash, "rahasia1"))


if __name__ == "__main__":
    unittest.main()
