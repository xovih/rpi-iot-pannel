"""Render smoke-test UI panel tanpa backend/GPIO fisik.

    python tests/render_ui.py [output.png] [tab] [mode]

Mode: `keyboard`, `status-demo`, `partial` (backend hanya menaungi I.1),
`queue` (antrean berisi peti dengan langkah routing PD).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from cakra_iot.config import AppConfig
from cakra_iot.storage import LocalStore
from cakra_iot.ui import PanelWindow

NAMES = {
    "I.1": "REGANG 1 DAN 2",
    "I.9-1": "Mal Ukur 1",
    "I.9-2": "Mal Ukur 2",
    "I.8-1": "Bubut Pinggir 1",
    "I.8-2": "Bubut Pinggir 2",
}

QUEUE_ROW = {
    "id": "q-1",
    "status": "RUNNING",
    "operationCode": "0020",
    "operationName": "REGANG POTONG",
    "workCenterCode": "1115LE01",
    "step": 2,
    "totalSteps": 8,
    "routingVersion": 2,
    "lotQty": 1000,
    "processedQty": 400,
    "remainingQty": 600,
    "lotPeti": {
        "petiId": "PETI_0002",
        "nomorPd": "1120000890",
        "order": {"materialCode": "MU5TJ", "materialDesc": "MUKAL 5,56X44mm 5TJ"},
    },
}


class PreviewApi:
    def __init__(self, partial: bool = False, queue: bool = False) -> None:
        self.partial = partial
        self.with_queue = queue
        self.last_samples: list[dict[str, Any]] = []
        self.queue_machine_codes: list[str] = []

    def bootstrap(self) -> dict[str, Any]:
        dals = ["I.1"] if self.partial else list(NAMES)
        return {
            "code": "RPI-INT-01-TEST",
            "machines": [
                {
                    # Kode mesin backend sengaja berbeda dari DAL: panel wajib
                    # memakai kode ini, bukan nilai lokal apa pun.
                    "code": f"PREVIEW_{index}",
                    "dalCode": dal,
                    "name": NAMES[dal],
                    "workCenters": [{"code": "1115LE01", "name": "REGANG 1 DAN 2"}],
                }
                for index, dal in enumerate(dals, start=1)
            ],
        }

    def queue(self, machine_code: str) -> list[dict[str, Any]]:
        self.queue_machine_codes.append(machine_code)
        if not (self.with_queue and machine_code == "PREVIEW_1"):
            return []
        queued = {**QUEUE_ROW, "id": "q-2", "status": "QUEUED", "lotPeti": {**QUEUE_ROW["lotPeti"], "petiId": "PETI_0003"}}
        return [dict(QUEUE_ROW), queued]

    def add_queue(self, machine_code: str, peti_id: str) -> dict[str, Any]:
        # Peti PETI_0002 masih berjalan di PREVIEW_1 (I.1) walau dipindai di tab lain.
        return {**QUEUE_ROW, "resumed": True, "machine": {"code": "PREVIEW_1", "dalCode": "I.1"}}

    def ingest(self, samples: list[dict[str, Any]]) -> dict[str, Any]:
        self.last_samples = samples
        return {"accepted": len(samples), "duplicate": 0, "rejected": 0, "unknownMachines": []}

    def heartbeat(self) -> None:
        return None


def main() -> int:
    root = Path(__file__).parents[1]
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / ".test-tmp" / "panel-ui.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    for stale in (output.with_suffix(".db"), output.with_suffix(".csv")):
        stale.unlink(missing_ok=True)
    config = AppConfig.load(root / "config.example.json")
    store = LocalStore(output.with_suffix(".db"), output.with_suffix(".csv"))
    app = QApplication([])
    mode = sys.argv[3] if len(sys.argv) > 3 else ""
    api = PreviewApi(partial=mode == "partial", queue=mode in ("queue", "resume"))
    if mode == "resume":
        # State tersimpan sebelum "listrik padam".
        store.set_state("operators:PREVIEW_1", [{"npp": "05818", "name": "Solihan"}])
        store.set_state("selected_machine", "PREVIEW_2")
    window = PanelWindow(config, api, store)  # type: ignore[arg-type]
    window.resize(1240, 1000)
    window.bootstrap()
    selected_tab = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if mode == "resume":
        assert window.stack.currentIndex() == 1, "tab terakhir harus dipulihkan"
        assert window.pages[0].operators == [{"npp": "05818", "name": "Solihan"}]
        messages: list[str] = []
        window.info = lambda _title, text: messages.append(text)  # type: ignore[method-assign]
        window.pages[1].peti_scan.setText("PETI_0002")
        window.pages[1].add_scanned_peti()
        assert window.stack.currentIndex() == 0 and messages, (window.stack.currentIndex(), messages)
        assert window.pages[0].current_queue_id() == "q-1"
        selected_tab = 0
    window.select_machine(selected_tab)
    assert all(len(page.slot_cards) == 2 for page in window.pages)
    window.show()
    app.processEvents()
    page = window.pages[selected_tab]
    page.peti_scan.setFocus()
    app.processEvents()
    window.keyboard_panel.insert("P")
    assert page.peti_scan.text() == "P"
    page.peti_scan.clear()
    assert "1115LE01" in page.peti_scan.placeholderText()
    heights = {button.height() for button in [*window.tab_buttons, window.keyboard_button, window.close_button]}
    assert len(heights) == 1, heights
    stack_before = window.stack.geometry()
    if mode == "keyboard":
        window.toggle_keyboard()
        app.processEvents()
        # Area antrean/operator tidak boleh menciut saat keyboard tampil.
        assert window.stack.geometry() == stack_before, (stack_before, window.stack.geometry())
    status_demo = mode == "status-demo"
    if status_demo:
        window.online = True
        window.pages[1].queue = [{"status": "RUNNING"}]
        window.store.increment(window.pages[1].machine.slots[0].code)
        window.pages[2].queue = [{"status": "RUNNING"}]
        window.refresh_tabs()

    def capture() -> None:
        assert window.tab_buttons[0].text() == "I.1\nREGANG 1 DAN 2"
        assert window.keyboard_button.property("expanded") == window.keyboard_panel.isVisible()
        if status_demo:
            assert "#3212ef" in window.tab_buttons[0].styleSheet()
            assert "#4fd13f" in window.tab_buttons[1].styleSheet()
            assert "#ff3b18" in window.tab_buttons[2].styleSheet()
            assert "#575252" in window.tab_buttons[3].styleSheet()
        if mode == "queue":
            process = window.pages[0].detail.item(4, 1).text()
            assert process == "0020 REGANG POTONG (2/8) · 1115LE01 · routing v2", process
            assert window.pages[0].action.text() == "FINISH"
        if mode == "partial":
            # Outbox lama berisi kode mesin lokal yang tidak lagi dikenal.
            window.store.enqueue(
                "stale-local-machine-code",
                "telemetry",
                {"machineCode": "1115LE01", "counterTotal": 1},
            )
            window.sync()
            assert window.device_authorized
            assert [page.machine.code for page in window.pages] == ["PREVIEW_1"]
            assert len(window.tab_buttons) == 1
            assert api.queue_machine_codes and set(api.queue_machine_codes) == {"PREVIEW_1"}
            assert api.last_samples and {
                sample["machineCode"] for sample in api.last_samples
            } == {"PREVIEW_1"}
            assert not any(
                row["event_id"] == "stale-local-machine-code"
                for row in window.store.pending()
            )
        else:
            assert len(window.tab_buttons) == 5
        window.grab().save(str(output))
        window.close()
        app.quit()

    QTimer.singleShot(800, capture)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
