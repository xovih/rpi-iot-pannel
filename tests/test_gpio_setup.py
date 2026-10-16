from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cakra_iot.config import AppConfig
from cakra_iot.genconfig import apply_pins, parse_pin
from cakra_iot.gpiotest import main as gpiotest_main


def write(directory: str, **extra: object) -> Path:
    path = Path(directory) / "config.json"
    raw = {"api_base_url": "http://x:4000", "device_token": "t", **extra}
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class GpioConfigTest(unittest.TestCase):
    def test_defaults_match_optocoupler_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig.load(write(directory))
            self.assertEqual((config.gpio_pull, config.gpio_edge, config.gpio_bounce_ms), ("up", "falling", 110))

    def test_physical_pin_numbers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, counters=[{"dal": "K.1", "slots": [{"code": "K.1", "pins": [37]}]}])
            with self.assertRaisesRegex(ValueError, "BCM"):
                AppConfig.load(path)

    def test_invalid_edge_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                AppConfig.load(write(directory, gpio_edge="naik"))


class PinArgumentTest(unittest.TestCase):
    def test_parse_single_and_spur(self) -> None:
        self.assertEqual(parse_pin("k.1=17,18"), ("K.1", "K.1", [17, 18]))
        self.assertEqual(parse_pin("K.2:spur1=5"), ("K.2", "K.2-SPUR1", [5]))
        with self.assertRaises(ValueError):
            parse_pin("K.1")

    def test_pins_replace_slots_of_named_dal_only(self) -> None:
        counters = [
            {"dal": "K.1", "slots": []},
            {"dal": "K.2", "slots": [{"code": "K.2", "pins": [4]}]},
        ]
        merged = apply_pins(counters, ["K.1=17", "K.3:SPUR1=5", "K.3:SPUR2=6"])
        self.assertEqual(merged[0]["slots"], [{"code": "K.1", "pins": [17]}])
        self.assertEqual(merged[1]["slots"], [{"code": "K.2", "pins": [4]}])
        self.assertEqual([slot["code"] for slot in merged[2]["slots"]], ["K.3-SPUR1", "K.3-SPUR2"])


class GpioTestToolTest(unittest.TestCase):
    def test_without_driver_prints_install_hint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, counters=[{"dal": "K.1", "slots": [{"code": "K.1", "pins": [17]}]}])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = gpiotest_main(["--config", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("python3-rpi-lgpio", output.getvalue())


if __name__ == "__main__":
    unittest.main()
