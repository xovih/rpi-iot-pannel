"""Ambang IDLE panel (revisi 19 Okt 2026): 180 detik, dikirim backend."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt5 import sip  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from cakra_iot.config import IDLE_AFTER_SECONDS, AppConfig  # noqa: E402
from cakra_iot.storage import LocalStore  # noqa: E402
from cakra_iot.ui import IDLE_KEY, PanelWindow, idle_threshold  # noqa: E402
from render_ui import PreviewApi  # noqa: E402

ROOT = Path(__file__).parents[1]
APP = QApplication.instance() or QApplication([])


class IdleThresholdTest(unittest.TestCase):
    def test_default_is_180_seconds(self) -> None:
        self.assertEqual(IDLE_AFTER_SECONDS, 180)
        self.assertEqual(AppConfig.load(ROOT / "config.example.json").idle_after_seconds, 180)

    def test_invalid_values_fall_back(self) -> None:
        self.assertEqual(idle_threshold("240", 180), 240)
        for bad in (None, "x", 0, 2, 99_999):
            self.assertEqual(idle_threshold(bad, 180), 180)


class MachineStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.store = LocalStore(folder / "panel.db", folder / "recovery.csv")
        self.api = PreviewApi(queue=True)
        config = AppConfig.load(ROOT / "config.example.json")
        self.window = PanelWindow(config, self.api, self.store)  # type: ignore[arg-type]
        self.window.bootstrap()

    def tearDown(self) -> None:
        # Hapus objek Qt langsung (tanpa closeEvent/event loop) sebelum SQLite
        # ditutup, agar timer panel tidak menyentuh basis data yang tertutup.
        sip.delete(self.window)
        self.store.close()
        self.tmp.cleanup()

    def test_run_until_180_seconds_without_pulse(self) -> None:
        page = self.window.pages[0]
        self.window.on_pulse(page.machine.slots[0].code)
        now = datetime.now(timezone.utc)
        self.assertEqual(self.window.machine_state(page, now + timedelta(seconds=170)), "RUN")
        self.assertEqual(self.window.machine_state(page, now + timedelta(seconds=185)), "IDLE")

    def test_backend_value_applies_and_persists(self) -> None:
        self.api.idle_after_seconds = 60
        self.window.bootstrap()
        self.assertEqual(self.window.idle_after_seconds, 60)
        self.assertEqual(self.store.get_state(IDLE_KEY), 60)
        page = self.window.pages[0]
        self.window.on_pulse(page.machine.slots[0].code)
        now = datetime.now(timezone.utc)
        self.assertEqual(self.window.machine_state(page, now + timedelta(seconds=65)), "IDLE")


if __name__ == "__main__":
    unittest.main()
