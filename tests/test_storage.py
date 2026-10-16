from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cakra_iot.config import AppConfig
from cakra_iot.storage import LocalStore


class StorageTest(unittest.TestCase):
    def test_counter_and_outbox_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalStore(root / "panel.db", root / "recovery.csv")
            self.assertEqual(store.increment("I.1"), 1)
            store.start_session(["I.1"])
            self.assertEqual(store.increment("I.1"), 2)
            self.assertEqual(store.session_counter("I.1"), 1)
            store.enqueue("event-1", "finish", {"yield_qty": 1})
            store.close()

            recovered = LocalStore(root / "panel.db", root / "recovery.csv")
            self.assertEqual(recovered.counter("I.1"), 2)
            self.assertEqual(recovered.session_counter("I.1"), 1)
            self.assertEqual(len(recovered.pending()), 1)
            self.assertTrue((root / "recovery.csv").read_text(encoding="utf-8").startswith("recorded_at"))
            recovered.close()


class ConfigTest(unittest.TestCase):
    def write(self, directory: str, raw: dict[str, object]) -> Path:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_counters_are_optional_and_not_limited_to_five_machines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"api_base_url": "http://x:4000/", "device_token": "t"})
            config = AppConfig.load(path)
            self.assertEqual(config.counters, ())
            self.assertEqual(config.api_base_url, "http://x:4000")
            # Path data relatif terhadap folder config, bukan folder kerja
            # proses — penting saat panel dibuka dengan klik-ganda.
            self.assertEqual(config.database_path, Path(directory).resolve() / "data" / "panel.db")

    def test_legacy_machines_key_is_still_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                {
                    "api_base_url": "http://x:4000",
                    "device_token": "t",
                    "machines": [{"code": "1115LE01", "dal": "i.1", "name": "Regang", "slots": [{"code": "I.1", "pin": 17}]}],
                },
            )
            config = AppConfig.load(path)
            self.assertEqual(config.counter_for("I.1").slots[0].pins, (17,))  # type: ignore[union-attr]

    def test_duplicate_pins_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                {
                    "api_base_url": "http://x:4000",
                    "device_token": "t",
                    "counters": [
                        {"dal": "A", "slots": [{"code": "A", "pins": [5]}]},
                        {"dal": "B", "slots": [{"code": "B", "pins": [5]}]},
                    ],
                },
            )
            with self.assertRaises(ValueError):
                AppConfig.load(path)

    def test_panel_14_gpio_mapping_accepts_two_pins_for_one_counter(self) -> None:
        source = Path(__file__).parents[1] / "config.example.json"
        config = AppConfig.load(source)
        self.assertEqual(config.api_base_url, "http://192.168.196.196:4000")
        self.assertEqual(config.counter_for("I.1").slots[0].pins, (17, 18))  # type: ignore[union-attr]
        self.assertEqual([pin for slot in config.all_slots for pin in slot.pins], [17, 18, 27, 22, 5, 6, 26, 16])


if __name__ == "__main__":
    unittest.main()
