"""Render smoke-test UI panel tanpa backend/GPIO fisik.

    python tests/render_ui.py [output.png] [tab] [mode]

Mode: `keyboard`, `status-demo`, `partial` (backend hanya menaungi I.1),
`queue` (antrean berisi peti dengan langkah routing PD), `settings` (tombol
PENGATURAN, dialog password, dan dialog pengaturan), `recover` (listrik padam
saat sesi berjalan → panel dibuka tanpa backend, counter dipulihkan & dijeda).
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

# Pin dari backend (menu Gateway → Pin GPIO). I.8-2 sengaja hanya Spur 2
# yang aktif (Spur 1 sedang diperbaiki).
BACKEND_SLOTS = {
    "I.1": [(1, [17])],
    "I.9-1": [(1, [27])],
    "I.9-2": [(1, [22])],
    "I.8-1": [(1, [5]), (2, [6])],
    "I.8-2": [(2, [16])],
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
            "gpio": {"managed": True, "pull": "up", "edge": "falling", "bounceMs": 60, "version": "v1"},
            "machines": [
                {
                    # Kode mesin backend sengaja berbeda dari DAL: panel wajib
                    # memakai kode ini, bukan nilai lokal apa pun.
                    "code": f"PREVIEW_{index}",
                    "dalCode": dal,
                    "name": NAMES[dal],
                    "workCenters": [{"code": "1115LE01", "name": "REGANG 1 DAN 2"}],
                    "counterSlots": [
                        {"slotNo": no, "code": f"{dal}-SPUR{no}", "pins": pins}
                        for no, pins in BACKEND_SLOTS[dal]
                    ],
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


class OfflineApi(PreviewApi):
    def bootstrap(self) -> dict[str, Any]:
        from cakra_iot.api import ApiError

        raise ApiError(None, "Backend tidak dapat dijangkau (simulasi).")


def run_recover(app: QApplication, config: AppConfig, output: Path) -> int:
    """Sesi berjalan, 37 pulsa, lalu proses mati tanpa closeEvent."""
    import cakra_iot.ui as ui_module
    from cakra_iot.dialogs import PanelDialog

    database, csv_path = output.with_suffix(".db"), output.with_suffix(".csv")
    store = LocalStore(database, csv_path)
    first = PanelWindow(config, PreviewApi(queue=True), store)  # type: ignore[arg-type]
    first.bootstrap()
    assert not first.unexpected_exit
    page = first.pages[0]
    assert page.running_row() and page.action.text() == "FINISH"
    for _ in range(37):
        first.on_pulse("I.1-SPUR1")
    assert page.current_output() == 37
    # "Listrik padam": tidak ada closeEvent, penanda app_running tetap 1.
    # Jendela dihancurkan tanpa closeEvent agar timer/slot tertunda ikut mati.
    from PyQt5 import sip

    first.gpio.close()
    sip.delete(first)
    store.close()

    notices: list[str] = []
    ui_module.alert = lambda _parent, _title, text, _tone="error": notices.append(text)  # type: ignore[assignment]
    store = LocalStore(database, csv_path)
    second = PanelWindow(config, OfflineApi(queue=True), store)  # type: ignore[arg-type]
    second.resize(1240, 1000)
    second.show()
    app.processEvents()
    assert second.unexpected_exit
    assert not second.online
    assert [p.machine.dal for p in second.pages][:1] == ["I.1"], "tab dari cache bootstrap"
    page = second.pages[0]
    assert page.recovered and page.running_row()["lotPeti"]["petiId"] == "PETI_0002"
    page.refresh_counters()
    assert page.current_card.value.text() == "37", page.current_card.value.text()
    assert page.total_card.value.text() == "437", page.total_card.value.text()
    assert page.action.text() == "START" and page.recovery_banner.isVisible()
    second.on_pulse("I.1-SPUR1")  # dijeda: tidak dihitung
    assert page.current_output() == 37
    message = second.recovery_message() or ""
    assert "exit yang tak terduga" in message and "PETI_0002" in message and "37" in message, message
    second.grab().save(str(output))
    notice = PanelDialog(second, "Data dipulihkan", message, "warning")
    notice.show()
    app.processEvents()
    notice.grab().save(str(output.with_name(output.stem + "-notice.png")))
    notice.close()

    page.start_or_finish()  # START: counter berjalan lagi dari 37
    assert not page.recovered and page.action.text() == "FINISH"
    assert not page.recovery_banner.isVisible()
    second.on_pulse("I.1-SPUR1")
    assert page.current_output() == 38
    second.close()
    # Ditutup normal: pembukaan berikutnya bukan exit tak terduga.
    store = LocalStore(database, csv_path)
    assert not store.begin_run()
    store.close()
    app.quit()
    return 0


def check_settings(window: PanelWindow, config: AppConfig, output: Path) -> None:
    """Tombol PENGATURAN di kanan bawah → password → dialog pengaturan."""
    import dataclasses
    import json
    import shutil

    from cakra_iot.panel_password import Gatekeeper, verify_password
    from cakra_iot.settings_dialog import PasswordDialog, SettingsDialog

    button = window.settings_button.geometry()
    status = window.status.geometry()
    assert button.left() > status.left() and button.top() >= status.top() - 10, (button, status)
    assert window.width() - button.right() < 40, (window.width(), button)

    gate = Gatekeeper()
    login = PasswordDialog(window, config, gate)
    login.show()
    login.password.setFocus()
    for key in "gw_live":
        login.findChild(type(window.keyboard_panel)).insert(key)
    assert login.password.text() == "gw_live", login.password.text()
    login.grab().save(str(output.with_name(output.stem + "-password.png")))
    login.password.setText("salah")
    login.submit()
    assert login.result() == 0 and login.error_label.text() == "Password salah."
    login.password.setText(config.device_token)
    login.submit()
    assert login.result() == 1

    target = output.with_name(output.stem + "-config.json")
    shutil.copy(Path(__file__).parents[1] / "config.example.json", target)
    dialog = SettingsDialog(window, dataclasses.replace(config, source=target))
    dialog.bootstrap = lambda: {"code": "RPI-INT-01-TEST", "machines": []}  # type: ignore[method-assign]
    dialog.show()
    dialog.grab().save(str(output.with_name(output.stem + "-settings.png")))
    dialog.api_url.setText("http://10.1.1.1:4000/")
    dialog.save()
    assert not dialog.result() and "Password" in dialog.error_label.text(), dialog.error_label.text()
    dialog.new_password.setText("rahasia1")
    dialog.repeat_password.setText("rahasia1")
    dialog.save()
    assert dialog.result() == 1, dialog.error_label.text()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["api_base_url"] == "http://10.1.1.1:4000"
    assert saved["device_token"] == config.device_token
    assert verify_password(saved["settings_password_hash"], "rahasia1")
    assert saved["counters"], "pin cadangan tidak boleh hilang"


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
    if mode == "recover":
        store.close()
        return run_recover(app, config, output)
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
            page0 = window.pages[0]
            # Sesi RUNNING tanpa baseline lokal: hitungan mulai dari sekarang.
            for _ in range(3):
                window.store.increment(page0.machine.slots[0].code)
            page0.refresh_counters()
            assert page0.current_card.value.text() == "3", page0.current_card.value.text()
            assert page0.slot_cards[0].value.text() == "3"
            assert page0.slot_cards[1].value.text() == "—"
            assert page0.total_card.value.text() == "403", page0.total_card.value.text()
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
            # Pin dari backend menggantikan config.json dan tersimpan di cache.
            assert window.pin_plan.source == "backend" and window.pin_plan.bounce_ms == 60
            assert window.store.get_state("gpio_pins")["counters"]["I.1"][0]["code"] == "I.1-SPUR1"
            spur2_only = next(page for page in window.pages if page.machine.dal == "I.8-2")
            assert [slot is None for slot in spur2_only.spur_slots()] == [True, False]
        window.grab().save(str(output))
        if mode == "settings":
            check_settings(window, config, output)
        window.close()
        app.quit()

    QTimer.singleShot(800, capture)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
