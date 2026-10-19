"""Pergantian peti otomatis saat qty peti terpenuhi (revisi 19 Okt 2026)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt5 import sip  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import cakra_iot.ui as ui_module  # noqa: E402
from cakra_iot.config import AppConfig  # noqa: E402
from cakra_iot.storage import LocalStore  # noqa: E402
from cakra_iot.ui import PanelWindow  # noqa: E402
from render_ui import QUEUE_ROW, PreviewApi  # noqa: E402

ROOT = Path(__file__).parents[1]
APP = QApplication.instance() or QApplication([])


class QueueApi(PreviewApi):
    """Antrean PREVIEW_1: peti penuh di 1.000 (400 sudah terproses) + satu peti antre."""

    def __init__(self) -> None:
        super().__init__(queue=True)
        running = {**QUEUE_ROW, "id": "q-1"}
        queued = {
            **QUEUE_ROW,
            "id": "q-2",
            "status": "QUEUED",
            "processedQty": 0,
            "lotPeti": {**QUEUE_ROW["lotPeti"], "petiId": "PETI_0003"},
        }
        self.rows: list[dict[str, Any]] = [running, queued]
        self.finished: list[dict[str, Any]] = []
        self.started: list[tuple[str, list[str]]] = []

    def queue(self, machine_code: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows] if machine_code == "PREVIEW_1" else []

    def finish(self, **payload: Any) -> dict[str, Any]:
        self.finished.append(payload)
        self.rows = [row for row in self.rows if row["id"] != payload["queue_id"]]
        return {}

    def start(self, queue_id: str, operators: list[str]) -> dict[str, Any]:
        self.started.append((queue_id, operators))
        for row in self.rows:
            if row["id"] == queue_id:
                row["status"] = "RUNNING"
        return {}


class ChangeoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.store = LocalStore(folder / "panel.db", folder / "recovery.csv")
        self.api = QueueApi()
        self.window = PanelWindow(AppConfig.load(ROOT / "config.example.json"), self.api, self.store)  # type: ignore[arg-type]
        self.window.bootstrap()
        self.page = self.window.pages[0]
        self.page.operators = [{"npp": "05818", "name": "Solihan"}]
        self.slot = self.page.machine.slots[0].code
        self.choice = "LANJUT"
        self.pulses_during_popup = 0
        test = self

        def fake_exec(dialog: Any) -> int:
            # Pulsa yang masuk selama popup tampil.
            for _ in range(test.pulses_during_popup):
                test.window.on_pulse(test.slot)
            dialog.choice = test.choice
            return 1

        self.original_exec = ui_module.CountdownDialog.exec_
        ui_module.CountdownDialog.exec_ = fake_exec  # type: ignore[method-assign]

    def tearDown(self) -> None:
        ui_module.CountdownDialog.exec_ = self.original_exec  # type: ignore[method-assign]
        sip.delete(self.window)
        self.store.close()
        self.tmp.cleanup()

    def pulse(self, count: int) -> None:
        for _ in range(count):
            self.window.on_pulse(self.slot)
        APP.processEvents()

    def test_full_peti_finishes_and_next_starts_with_carry_over(self) -> None:
        self.pulses_during_popup = 7
        self.pulse(599)
        self.assertEqual(self.api.finished, [])
        self.pulse(1)  # 400 + 600 = 1.000 → penuh
        self.assertEqual(len(self.api.finished), 1)
        self.assertEqual(self.api.finished[0]["yield_qty"], 600)
        self.assertFalse(self.api.finished[0]["keep_in_queue"])
        self.assertEqual(self.api.started, [("q-2", ["05818"])])
        # 7 pulsa saat popup tampil dibawa ke peti berikutnya.
        self.assertEqual(self.page.current_output(), 7)

    def test_hold_keeps_session_and_does_not_ask_again(self) -> None:
        self.choice = "TAHAN"
        self.pulse(600)
        self.pulse(5)
        self.assertEqual(self.api.finished, [])
        self.assertEqual(self.api.started, [])
        self.assertIn("q-1", self.page.changeover_declined)


if __name__ == "__main__":
    unittest.main()
