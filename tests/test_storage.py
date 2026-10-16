from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cakra_iot.config import AppConfig, merge_counters
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

    def test_unexpected_exit_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalStore(root / "panel.db", root / "recovery.csv")
            self.assertFalse(store.begin_run())
            store.close()  # listrik padam: end_run tidak sempat dipanggil
            store = LocalStore(root / "panel.db", root / "recovery.csv")
            self.assertTrue(store.begin_run())
            store.end_run()
            store.close()
            store = LocalStore(root / "panel.db", root / "recovery.csv")
            self.assertFalse(store.begin_run())
            store.close()

    def test_ui_state_survives_power_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalStore(root / "panel.db", root / "recovery.csv")
            store.set_state("operators:I.1", [{"npp": "05818", "name": "Solihan"}])
            store.set_state("selected_machine", "I.1")
            store.close()  # setara proses mati mendadak setelah commit
            recovered = LocalStore(root / "panel.db", root / "recovery.csv")
            self.assertEqual(recovered.get_state("operators:I.1")[0]["npp"], "05818")
            self.assertEqual(recovered.get_state("selected_machine"), "I.1")
            self.assertEqual(recovered.get_state("tidak-ada", []), [])
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


class MergeCountersTest(unittest.TestCase):
    TEMPLATE = [
        {"dal": "I.1", "slots": [{"code": "I.1", "pins": [17, 18]}]},
        {"dal": "I.8-1", "slots": [{"code": "I.8-1-SPUR1", "pins": [5]}]},
    ]

    def test_wizard_config_with_empty_slots_gets_template_pins(self) -> None:
        existing = [{"dal": "I.1", "slots": []}, {"dal": "I.8-1", "slots": []}]
        merged = merge_counters(["i.1", "I.8-1", "X.9"], existing, self.TEMPLATE)
        self.assertEqual(merged[0]["slots"][0]["pins"], [17, 18])
        self.assertEqual(merged[1]["slots"][0]["code"], "I.8-1-SPUR1")
        self.assertEqual(merged[2], {"dal": "X.9", "slots": []})

    def test_technician_pins_are_kept(self) -> None:
        existing = [{"dal": "I.1", "slots": [{"code": "I.1", "pins": [4]}]}]
        merged = merge_counters(["I.1"], existing, self.TEMPLATE)
        self.assertEqual(merged[0]["slots"][0]["pins"], [4])

    def test_conflicting_pins_are_left_empty(self) -> None:
        existing = [{"dal": "A", "slots": [{"code": "A", "pins": [17]}]}]
        merged = merge_counters(["A", "I.1"], existing, self.TEMPLATE)
        self.assertEqual(merged[1]["slots"], [])


if __name__ == "__main__":
    unittest.main()
