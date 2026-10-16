from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .api import ApiClient, ApiError
from .config import AppConfig, CounterSlot
from .dialogs import alert, confirm
from .gpio import GpioManager
from .storage import LocalStore

PURPLE = "#3212ef"
RUN = "#4fd13f"
IDLE = "#ff3b18"
STOP = "#575252"
# Tinggi seragam seluruh tombol baris atas (tab mesin, keyboard, tutup).
TOP_BUTTON_HEIGHT = 64
# Tinggi area keyboard layar sentuh. Area ini selalu dipesan walau keyboard
# tersembunyi, sehingga tabel antrean/operator tidak menciut saat keyboard
# dibuka dan posisi kolom input tidak berpindah ketika operator mengetik.
KEYBOARD_HEIGHT = 205
# Mockup hanya memiliki empat status visual. Kondisi koneksi offline tetap
# dijelaskan pada footer, sedangkan tombol DAL memakai warna STOP.
OFFLINE = STOP


def number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


@dataclass(frozen=True)
class PanelMachine:
    """Satu tab mesin. Identitasnya **selalu** dari backend (revisi 16 Okt
    2026): kode mesin, DAL, nama, dan work center dibaca dari bootstrap sesuai
    mesin yang dinaungi gateway pada menu Gateway / Panel IoT. Dari config
    lokal hanya diambil pin counter yang dipasang pada DAL tersebut."""

    code: str
    dal: str
    name: str
    work_centers: tuple[str, ...]
    slots: tuple[CounterSlot, ...]


class FinishDialog(QDialog):
    def __init__(self, yield_qty: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Konfirmasi Finish")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        good = QLabel(number(yield_qty))
        good.setStyleSheet("font-size:32px;font-weight:800")
        self.reject_qty = QSpinBox()
        self.reject_qty.setRange(0, 10_000_000)
        self.obstacle = QLineEdit()
        self.obstacle.setPlaceholderText("Opsional — pilih/tulis kendala PMS")
        form.addRow("Hasil baik (counter)", good)
        form.addRow("Jumlah reject", self.reject_qty)
        form.addRow("Kendala", self.obstacle)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class CounterCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__(objectName="counterCard")
        layout = QVBoxLayout(self)
        caption = QLabel(title, alignment=Qt.AlignCenter)
        caption.setObjectName("counterCaption")
        self.value = QLabel("0", alignment=Qt.AlignCenter)
        self.value.setObjectName("counterValue")
        layout.addWidget(caption)
        layout.addWidget(self.value, 1)


class OnScreenKeyboard(QFrame):
    def __init__(self, target_provider: Callable[[], QLineEdit | None]) -> None:
        super().__init__(objectName="keyboardPanel")
        self.target_provider = target_provider
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setHorizontalSpacing(7)
        layout.setVerticalSpacing(6)
        for row, keys in enumerate(
            ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM_-."]
        ):
            for column, key in enumerate(keys):
                label = "GARIS _" if key == "_" else key
                layout.addWidget(self.key(label, key), row, column)
        layout.addWidget(self.key("BERSIHKAN", "CLEAR"), 4, 0, 1, 2)
        layout.addWidget(self.key("SPASI", "SPACE"), 4, 2, 1, 4)
        layout.addWidget(self.key("⌫ HAPUS", "BACKSPACE"), 4, 6, 1, 2)
        layout.addWidget(self.key("ENTER", "ENTER"), 4, 8, 1, 2)

    def key(self, label: str, value: str) -> QPushButton:
        button = QPushButton(label)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _checked, content=value: self.insert(content))
        return button

    def insert(self, value: str) -> None:
        target = self.target_provider()
        if target is None:
            return
        if value == "BACKSPACE":
            target.backspace()
        elif value == "CLEAR":
            target.clear()
        elif value == "SPACE":
            target.insert(" ")
        elif value == "ENTER":
            target.returnPressed.emit()
        else:
            target.insert(value)
        target.setFocus()


class MachinePage(QWidget):
    def __init__(self, machine: PanelMachine, host: "PanelWindow") -> None:
        super().__init__()
        self.machine = machine
        self.host = host
        self.queue: list[dict[str, Any]] = []
        self.operators: list[dict[str, str]] = []

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(18)

        left = QFrame(objectName="section")
        left.setMinimumWidth(410)
        left.setMaximumWidth(470)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(22, 18, 22, 18)
        left_layout.setSpacing(10)
        left_layout.addWidget(QLabel("ANTRIAN PETI", objectName="sectionTitle"))
        self.queue_table = self.table(["NOMOR PETI", "STATUS", "AKSI"])
        self.queue_table.setMinimumHeight(130)
        self.queue_table.cellClicked.connect(lambda row, _column: self.select_queue(row))
        left_layout.addWidget(self.queue_table)
        self.peti_scan = QLineEdit()
        # Work center mesin ditampilkan agar operator tahu peti PD mana yang
        # sah dipindai: routing PD harus melewati salah satunya.
        wc_hint = f" (WC {', '.join(machine.work_centers)})" if machine.work_centers else ""
        self.peti_scan.setPlaceholderText(f"Scan nomor peti{wc_hint} lalu Enter")
        self.peti_scan.returnPressed.connect(self.add_scanned_peti)
        left_layout.addWidget(self.peti_scan)

        left_layout.addWidget(QLabel("OPERATOR", objectName="sectionTitle"))
        self.operator_table = self.table(["NPP", "NAMA", "AKSI"])
        self.operator_table.setMinimumHeight(105)
        left_layout.addWidget(self.operator_table)
        self.operator_scan = QLineEdit()
        self.operator_scan.setPlaceholderText("Scan NPP operator lalu Enter")
        self.operator_scan.returnPressed.connect(self.add_operator)
        left_layout.addWidget(self.operator_scan)

        actions = QHBoxLayout()
        clear = QPushButton("CLEAR ALL", objectName="dangerButton")
        clear.clicked.connect(self.clear_inputs)
        self.action = QPushButton("START", objectName="primaryButton")
        self.action.clicked.connect(self.start_or_finish)
        actions.addWidget(clear)
        actions.addWidget(self.action)
        left_layout.addLayout(actions)
        outer.addWidget(left, 4)

        right = QFrame(objectName="section")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(14)
        right_layout.addWidget(QLabel("MONITORING", objectName="sectionTitle"))
        self.detail = self.table(["", ""])
        self.detail.horizontalHeader().hide()
        self.detail.verticalHeader().hide()
        self.detail.setRowCount(5)
        self.detail.setMinimumHeight(178)
        self.detail.setMaximumHeight(190)
        labels = [
            "NOMOR PD",
            "NOMOR PETI",
            "KODE MATERIAL",
            "DESKRIPSI MATERIAL",
            "PROSES",
        ]
        for row, label in enumerate(labels):
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemIsEnabled)
            item.setBackground(QColor("#eeeeee"))
            item.setFont(QFont("Arial", 10, QFont.Bold))
            self.detail.setItem(row, 0, item)
            self.detail.setItem(row, 1, QTableWidgetItem("—"))
        self.detail.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.detail.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        right_layout.addWidget(self.detail)

        counter_grid = QGridLayout()
        counter_grid.setSpacing(14)
        self.current_card = CounterCard("HASIL SAAT INI")
        self.total_card = CounterCard("TOTAL PENGERJAAN")
        counter_grid.addWidget(self.current_card, 0, 0)
        counter_grid.addWidget(self.total_card, 0, 1)
        self.slot_cards: list[CounterCard] = []
        # Dua posisi Spur selalu dipertahankan agar susunan seluruh tab sama
        # seperti mockup. Mesin satu-spur memakai Spur 1; Spur 2 ditandai tidak
        # tersedia dan tidak ikut dalam perhitungan.
        for index in range(2):
            card = CounterCard(f"SPUR {index + 1}")
            self.slot_cards.append(card)
            counter_grid.addWidget(card, 1, index)
        right_layout.addLayout(counter_grid, 1)
        outer.addWidget(right, 6)

    @staticmethod
    def table(headers: list[str]) -> QTableWidget:
        widget = QTableWidget(0, len(headers))
        widget.setHorizontalHeaderLabels(headers)
        widget.verticalHeader().hide()
        widget.setSelectionBehavior(QTableWidget.SelectRows)
        widget.setSelectionMode(QTableWidget.SingleSelection)
        widget.setEditTriggers(QTableWidget.NoEditTriggers)
        widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        widget.horizontalHeader().setSectionResizeMode(
            len(headers) - 1, QHeaderView.ResizeToContents
        )
        return widget

    def reload_queue(self) -> None:
        if not self.host.device_authorized or not self.host.online:
            return
        selected_id = self.current_queue_id()
        try:
            self.queue = self.host.api.queue(self.machine.code)
            self.render_queue(selected_id)
        except ApiError as exc:
            self.host.handle_api_error(exc)

    def render_queue(self, selected_id: str | None = None) -> None:
        self.queue_table.setRowCount(len(self.queue))
        select_row = 0
        for row_index, row in enumerate(self.queue):
            lot = row["lotPeti"]
            status = "DIKERJAKAN" if row["status"] == "RUNNING" else "DIANTRIKAN"
            self.queue_table.setItem(row_index, 0, QTableWidgetItem(lot["petiId"]))
            self.queue_table.setItem(row_index, 1, QTableWidgetItem(status))
            button = QPushButton("STOP" if row["status"] == "RUNNING" else "DEL")
            button.setObjectName("tableAction")
            button.clicked.connect(lambda _checked, i=row_index: self.queue_action(i))
            self.queue_table.setCellWidget(row_index, 2, button)
            if row["id"] == selected_id or row["status"] == "RUNNING":
                select_row = row_index
        if self.queue:
            self.queue_table.selectRow(select_row)
            self.select_queue(select_row)
        else:
            self.render_detail(None)
        self.refresh_action()

    def add_scanned_peti(self) -> None:
        peti = self.peti_scan.text().strip().upper()
        self.peti_scan.clear()
        if not peti:
            return
        if not self.host.can_validate_new_work():
            self.host.show_connection_warning()
            return
        try:
            # Backend memilih tahapan berikutnya menurut routing PD peti itu
            # sendiri, lalu menolak bila urutan routing belum dipenuhi.
            row = self.host.api.add_queue(self.machine.code, peti)
            self.reload_queue()
            self.select_queue_by_id(str(row["id"]))
        except ApiError as exc:
            self.host.handle_api_error(exc, dialog=True)

    def add_operator(self) -> None:
        npp = self.operator_scan.text().strip().upper()
        self.operator_scan.clear()
        if not npp or any(item["npp"] == npp for item in self.operators):
            return
        if not self.host.can_validate_new_work():
            self.host.show_connection_warning()
            return
        try:
            operator = self.host.api.resolve_operator(self.machine.code, npp)
            self.operators.append({"npp": operator["npp"], "name": operator["name"]})
            self.render_operators()
        except ApiError as exc:
            self.host.handle_api_error(exc, dialog=True)

    def render_operators(self) -> None:
        self.operator_table.setRowCount(len(self.operators))
        for row, operator in enumerate(self.operators):
            self.operator_table.setItem(row, 0, QTableWidgetItem(operator["npp"]))
            self.operator_table.setItem(row, 1, QTableWidgetItem(operator["name"]))
            remove = QPushButton("DEL")
            remove.setObjectName("tableAction")
            remove.clicked.connect(lambda _checked, i=row: self.remove_operator(i))
            self.operator_table.setCellWidget(row, 2, remove)

    def remove_operator(self, row: int) -> None:
        if 0 <= row < len(self.operators):
            self.operators.pop(row)
            self.render_operators()

    def queue_action(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            return
        self.queue_table.selectRow(index)
        self.select_queue(index)
        if self.queue[index]["status"] == "RUNNING":
            self.finish(self.queue[index])
        else:
            self.delete_queue(self.queue[index])

    def delete_queue(self, row: dict[str, Any]) -> None:
        if not self.host.can_validate_new_work():
            self.host.show_connection_warning()
            return
        try:
            self.host.api.delete_queue(row["id"])
            self.reload_queue()
        except ApiError as exc:
            self.host.handle_api_error(exc, dialog=True)

    def clear_inputs(self) -> None:
        self.operators.clear()
        self.render_operators()
        self.peti_scan.clear()
        self.operator_scan.clear()

    def start_or_finish(self) -> None:
        current = self.current_queue()
        if not current:
            self.host.warning("Pilih peti pada antrean terlebih dahulu.")
            return
        if current["status"] == "RUNNING":
            self.finish(current)
            return
        if not self.host.can_validate_new_work():
            self.host.show_connection_warning()
            return
        if not self.operators:
            self.host.warning("Scan minimal satu NPP operator yang valid.")
            return
        try:
            self.host.api.start(current["id"], [item["npp"] for item in self.operators])
            self.host.store.start_session([slot.code for slot in self.machine.slots])
            self.reload_queue()
        except ApiError as exc:
            self.host.handle_api_error(exc, dialog=True)

    def finish(self, current: dict[str, Any]) -> None:
        yield_qty = sum(
            self.host.store.session_counter(slot.code) for slot in self.machine.slots
        )
        dialog = FinishDialog(yield_qty, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        payload = {
            "queue_id": current["id"],
            "event_id": str(uuid.uuid4()),
            "yield_qty": yield_qty,
            "scrap_qty": dialog.reject_qty.value(),
            "reason": dialog.obstacle.text().strip() or None,
        }
        if self.host.online and self.host.device_authorized:
            try:
                self.host.api.finish(**payload)
                self.operators.clear()
                self.render_operators()
                self.reload_queue()
                return
            except ApiError as exc:
                if exc.status_code is not None:
                    self.host.handle_api_error(exc, dialog=True)
                    return
                self.host.handle_api_error(exc)
        self.host.store.enqueue(payload["event_id"], "finish", payload)
        self.host.warning(
            "Jaringan terputus. Finish aman di SQLite outbox dan akan dikirim ulang."
        )

    def current_queue_id(self) -> str | None:
        row = self.queue_table.currentRow()
        return str(self.queue[row]["id"]) if 0 <= row < len(self.queue) else None

    def current_queue(self) -> dict[str, Any] | None:
        row = self.queue_table.currentRow()
        return self.queue[row] if 0 <= row < len(self.queue) else None

    def select_queue_by_id(self, queue_id: str) -> None:
        for index, row in enumerate(self.queue):
            if str(row["id"]) == queue_id:
                self.queue_table.selectRow(index)
                self.select_queue(index)
                return

    def select_queue(self, index: int) -> None:
        self.render_detail(self.queue[index] if 0 <= index < len(self.queue) else None)
        self.refresh_action()

    @staticmethod
    def process_text(row: dict[str, Any]) -> str:
        """`0020 REGANG (2/8) · 1115LE01 · routing v2` — tahapan yang
        dikerjakan beserta posisinya pada routing PD."""
        name = row.get("operationName") or ""
        step, total = row.get("step"), row.get("totalSteps")
        position = f" ({step}/{total})" if step and total else ""
        version = row.get("routingVersion")
        suffix = f" · routing v{version}" if version else ""
        return f"{row['operationCode']} {name}{position} · {row['workCenterCode']}{suffix}".replace(
            "  ", " "
        )

    def render_detail(self, row: dict[str, Any] | None) -> None:
        values = ["—"] * 5
        if row:
            lot = row["lotPeti"]
            values = [
                lot["nomorPd"],
                lot["petiId"],
                lot["order"]["materialCode"],
                lot["order"]["materialDesc"],
                self.process_text(row),
            ]
        for index, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setFlags(Qt.ItemIsEnabled)
            self.detail.setItem(index, 1, item)

    def refresh_action(self) -> None:
        row = self.current_queue()
        self.action.setText("FINISH" if row and row["status"] == "RUNNING" else "START")

    def refresh_counters(self) -> None:
        current = sum(
            self.host.store.session_counter(slot.code) for slot in self.machine.slots
        )
        total = sum(self.host.store.counter(slot.code) for slot in self.machine.slots)
        self.current_card.value.setText(number(current))
        self.total_card.value.setText(number(total))
        for index, card in enumerate(self.slot_cards):
            if index < len(self.machine.slots):
                card.value.setText(
                    number(self.host.store.session_counter(self.machine.slots[index].code))
                )
            else:
                card.value.setText("—")


class PanelWindow(QMainWindow):
    def __init__(self, config: AppConfig, api: ApiClient, store: LocalStore) -> None:
        super().__init__()
        self.config, self.api, self.store = config, api, store
        self.online = False
        self.device_authorized = False
        self.connection_reason = "Belum terhubung ke backend."
        self.last_telemetry_ok: datetime | None = None
        self.pages: list[MachinePage] = []
        self.tab_buttons: list[QPushButton] = []
        self.last_input: QLineEdit | None = None

        self.setWindowTitle("CAKRA · Panel IoT")
        self.setMinimumSize(1180, 900)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 14)
        layout.setSpacing(12)

        # Tab mesin dibentuk dari bootstrap backend, bukan dari config lokal.
        self.tabs = QHBoxLayout()
        self.tabs.setSpacing(10)
        self.stack = QStackedWidget()
        self.keyboard_button = QPushButton("⌨")
        self.keyboard_button.setObjectName("keyboardToggle")
        self.keyboard_button.setFixedSize(72, TOP_BUTTON_HEIGHT)
        self.keyboard_button.setFocusPolicy(Qt.NoFocus)
        self.keyboard_button.setProperty("expanded", False)
        self.keyboard_button.clicked.connect(self.toggle_keyboard)
        self.tabs.addWidget(self.keyboard_button)
        # Jendela kiosk tidak memiliki tombol minimize/close; tombol ini
        # satu-satunya jalan keluar yang disengaja, selalu dengan konfirmasi.
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("closeApp")
        self.close_button.setToolTip("Tutup Panel IoT")
        self.close_button.setFixedSize(72, TOP_BUTTON_HEIGHT)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.confirm_close)
        self.tabs.addWidget(self.close_button)
        layout.addLayout(self.tabs)

        header = QFrame(objectName="machineHeader")
        header_layout = QHBoxLayout(header)
        self.heading = QLabel()
        self.heading.setObjectName("heading")
        header_layout.addWidget(self.heading, 1)
        for label, color in [
            ("MESIN AKTIF", PURPLE),
            ("MESIN JALAN", RUN),
            ("MESIN IDLE", IDLE),
            ("MESIN STOP", STOP),
        ]:
            legend = QWidget()
            legend_layout = QHBoxLayout(legend)
            legend_layout.setContentsMargins(0, 0, 0, 0)
            legend_layout.setSpacing(6)
            swatch = QLabel()
            swatch.setFixedSize(19, 19)
            swatch.setStyleSheet(f"background:{color}")
            legend_layout.addWidget(swatch)
            legend_layout.addWidget(QLabel(label, objectName="legendText"))
            header_layout.addWidget(legend)
        layout.addWidget(header)

        layout.addWidget(self.stack, 1)
        self.stack.hide()
        self.keyboard_panel = OnScreenKeyboard(self.keyboard_target)
        self.keyboard_panel.setFixedHeight(KEYBOARD_HEIGHT)
        policy = self.keyboard_panel.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.keyboard_panel.setSizePolicy(policy)
        self.keyboard_panel.hide()
        layout.addWidget(self.keyboard_panel)
        self.status = QLabel("MEMERIKSA BACKEND…")
        self.status.setObjectName("connectionStatus")
        layout.addWidget(self.status)
        self.setCentralWidget(root)
        self.setStyleSheet(self.styles())
        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self.remember_input)
        self.update_heading()

        self.gpio = GpioManager(config.all_slots, config.gpio_mode, self.on_pulse)
        self.gpio.start()
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh_ui)
        self.ui_timer.start(250)
        self.network_timer = QTimer(self)
        self.network_timer.timeout.connect(self.sync)
        self.network_timer.start(max(250, 1000 // config.telemetry_hz))
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.timeout.connect(self.bootstrap)
        self.reconnect_timer.start(10_000)
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self.heartbeat_fallback)
        self.heartbeat_timer.start(config.heartbeat_seconds * 1000)
        QTimer.singleShot(0, self.bootstrap)

    # ── Bootstrap & susunan tab ─────────────────────────────────────────────

    def bootstrap(self) -> None:
        try:
            bootstrap = self.api.bootstrap()
            backend_gateway_code = str(bootstrap.get("code", "")).upper()
            expected = self.config.gateway_code.upper()
            if expected and backend_gateway_code != expected:
                raise ApiError(
                    409,
                    f"Token ini milik gateway {backend_gateway_code or 'tanpa kode'}, "
                    f"bukan {self.config.gateway_code}.",
                )
            machines = [self.panel_machine(row) for row in bootstrap.get("machines", [])]
            self.apply_machines(machines)
            if not machines:
                self.device_authorized = False
                raise ApiError(
                    409,
                    f"Gateway {backend_gateway_code} belum menaungi mesin aktif. "
                    "Pilih mesinnya pada menu Gateway / Panel IoT di web CAKRA.",
                )
            self.device_authorized = True
            self.set_online(True)
            for page in self.pages:
                page.reload_queue()
        except ApiError as exc:
            self.handle_api_error(exc)

    def panel_machine(self, row: dict[str, Any]) -> PanelMachine:
        dal = str(row["dalCode"]).strip().upper()
        counter = self.config.counter_for(dal)
        return PanelMachine(
            code=str(row["code"]).strip().upper(),
            dal=dal,
            name=str(row.get("name") or dal),
            work_centers=tuple(
                str(item["code"]) for item in row.get("workCenters", []) if item.get("code")
            ),
            slots=counter.slots if counter else (),
        )

    def apply_machines(self, machines: list[PanelMachine]) -> None:
        """Membangun ulang tab hanya bila susunan mesin backend berubah, agar
        antrean dan operator yang sedang dipindai tidak hilang tiap 10 detik."""
        if [page.machine for page in self.pages] == machines:
            return
        current_code = self.current_page().machine.code if self.current_page() else None
        for button in self.tab_buttons:
            self.tabs.removeWidget(button)
            button.deleteLater()
        for page in self.pages:
            self.stack.removeWidget(page)
            page.deleteLater()
        self.tab_buttons, self.pages = [], []
        self.last_input = None
        for index, machine in enumerate(machines):
            button = QPushButton(f"{machine.dal}\n{machine.name}")
            button.setObjectName("machineTab")
            button.setFixedHeight(TOP_BUTTON_HEIGHT)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _checked, i=index: self.select_machine(i))
            self.tabs.insertWidget(index, button)
            self.tab_buttons.append(button)
            page = MachinePage(machine, self)
            self.pages.append(page)
            self.stack.addWidget(page)
        self.stack.setVisible(bool(self.pages))
        selected = next(
            (index for index, page in enumerate(self.pages) if page.machine.code == current_code),
            0,
        )
        if self.pages:
            self.stack.setCurrentIndex(selected)
        self.update_heading()
        self.refresh_tabs()

    def current_page(self) -> MachinePage | None:
        index = self.stack.currentIndex()
        return self.pages[index] if 0 <= index < len(self.pages) else None

    def select_machine(self, index: int) -> None:
        if not 0 <= index < len(self.pages):
            return
        self.stack.setCurrentIndex(index)
        self.update_heading()
        self.refresh_tabs()
        self.pages[index].reload_queue()

    def update_heading(self) -> None:
        page = self.current_page()
        if page is None:
            self.heading.setText("MENUNGGU MESIN DARI BACKEND")
            return
        self.heading.setText(f"DAL {page.machine.dal} — {page.machine.name.upper()}")

    # ── Keyboard layar sentuh ───────────────────────────────────────────────

    def toggle_keyboard(self) -> None:
        show = not self.keyboard_panel.isVisible()
        self.keyboard_panel.setVisible(show)
        self.keyboard_button.setProperty("expanded", show)
        self.keyboard_button.style().unpolish(self.keyboard_button)
        self.keyboard_button.style().polish(self.keyboard_button)
        if show:
            target = self.keyboard_target()
            if target is not None:
                target.setFocus()

    def confirm_close(self) -> None:
        if confirm(
            self,
            "Tutup Panel IoT",
            "Tutup aplikasi Panel IoT CAKRA? Counter dan Finish yang belum terkirim "
            "tetap tersimpan dan dikirim saat panel dibuka kembali.",
            yes="TUTUP",
        ):
            self.close()

    def remember_input(self, _old: QWidget | None, current: QWidget | None) -> None:
        if isinstance(current, QLineEdit):
            self.last_input = current

    def keyboard_target(self) -> QLineEdit | None:
        if self.last_input is not None:
            try:
                if self.last_input.isVisible() and self.last_input.isEnabled():
                    return self.last_input
            except RuntimeError:
                self.last_input = None
        page = self.current_page()
        return page.peti_scan if page else None

    # ── Counter, status tab, dan telemetry ──────────────────────────────────

    def on_pulse(self, slot_code: str) -> None:
        self.store.increment(slot_code)

    def refresh_ui(self) -> None:
        for page in self.pages:
            page.refresh_counters()
        self.refresh_tabs()

    def machine_state(self, page: MachinePage, now: datetime) -> str:
        if not any(row["status"] == "RUNNING" for row in page.queue):
            return "STOP"
        pulses = [self.store.last_pulse(slot.code) for slot in page.machine.slots]
        recent = any(
            pulse
            and (now - datetime.fromisoformat(pulse)).total_seconds()
            < self.config.idle_after_seconds
            for pulse in pulses
        )
        return "RUN" if recent else "IDLE"

    def refresh_tabs(self) -> None:
        now = datetime.now(timezone.utc)
        for index, page in enumerate(self.pages):
            if index == self.stack.currentIndex():
                color = PURPLE
            elif not self.online:
                color = OFFLINE
            else:
                color = {"RUN": RUN, "IDLE": IDLE}.get(self.machine_state(page, now), STOP)
            self.tab_buttons[index].setStyleSheet(f"background:{color};color:white")

    def sync(self) -> None:
        if not self.device_authorized:
            return
        now = datetime.now(timezone.utc)
        samples: list[dict[str, Any]] = []
        for page in self.pages:
            state = self.machine_state(page, now)
            for slot in page.machine.slots:
                samples.append(
                    {
                        "eventId": str(uuid.uuid4()),
                        # Selalu kode dari backend; config lokal tidak lagi
                        # menyimpan kode mesin.
                        "machineCode": page.machine.code,
                        "logicalSlot": slot.code,
                        "counterTotal": self.store.counter(slot.code),
                        "delta": 0,
                        "status": state,
                        "recordedAt": now.isoformat(),
                    }
                )
        try:
            self.flush_outbox()
            if samples:
                result = self.api.ingest(samples)
                if isinstance(result, dict) and result.get("unknownMachines"):
                    # Mesin dipindah/dinonaktifkan di backend: muat ulang tab.
                    QTimer.singleShot(0, self.bootstrap)
            self.last_telemetry_ok = now
            self.set_online(True)
        except ApiError as exc:
            if exc.status_code is None:
                for sample in samples:
                    self.store.enqueue(sample["eventId"], "telemetry", sample)
            self.handle_api_error(exc)

    def flush_outbox(self) -> None:
        rows = self.store.pending(100)
        telemetry = [row for row in rows if row["kind"] == "telemetry"]
        finishes = [row for row in rows if row["kind"] == "finish"]
        if telemetry:
            active_codes = {page.machine.code for page in self.pages}
            decoded = [(row, json.loads(row["payload"])) for row in telemetry]
            eligible = [
                (row, payload)
                for row, payload in decoded
                if str(payload.get("machineCode", "")).upper() in active_codes
            ]
            stale = [
                row["event_id"]
                for row, payload in decoded
                if str(payload.get("machineCode", "")).upper() not in active_codes
            ]
            # Counter total bersifat monotonik. Sampel lama dari mesin yang
            # tidak lagi dinaungi panel aman dibuang; saat mesin kembali,
            # total terbaru dikirim pada siklus berikutnya.
            self.store.acknowledge(stale)
            if eligible:
                self.api.ingest([payload for _, payload in eligible])
                self.store.acknowledge([row["event_id"] for row, _ in eligible])
        for row in finishes:
            payload = json.loads(row["payload"])
            self.api.finish(**payload)
            self.store.acknowledge([row["event_id"]])

    def heartbeat_fallback(self) -> None:
        if not self.device_authorized:
            return
        now = datetime.now(timezone.utc)
        if self.last_telemetry_ok and (
            now - self.last_telemetry_ok
        ).total_seconds() < self.config.heartbeat_seconds:
            return
        try:
            self.api.heartbeat()
            self.set_online(True)
        except ApiError as exc:
            self.handle_api_error(exc)

    # ── Status koneksi & pesan ──────────────────────────────────────────────

    def handle_api_error(self, error: ApiError, dialog: bool = False) -> None:
        if error.status_code == 401:
            self.device_authorized = False
            reason = (
                f"Backend {self.config.api_base_url} aktif, tetapi device token ditolak. "
                "Terbitkan/rotasi token gateway lalu isi config.json."
            )
        elif error.status_code is None:
            reason = str(error)
        else:
            reason = f"Backend menolak permintaan (HTTP {error.status_code}): {error}"
        rejected = error.status_code is not None and 400 <= error.status_code < 500
        if dialog and rejected and error.status_code != 401:
            # Penolakan validasi (peti salah routing, operator tanpa akses)
            # bukan tanda backend putus: cukup tampilkan, status tetap ONLINE.
            self.error(str(error))
            return
        self.set_online(False, reason)
        if dialog:
            self.error(reason)

    def set_online(self, online: bool, reason: str | None = None) -> None:
        self.online = online
        if reason:
            self.connection_reason = reason
        if online:
            self.connection_reason = "Backend dan device token tervalidasi."
            self.status.setText(
                f"ONLINE  ·  {self.config.api_base_url}  ·  {len(self.pages)} mesin  ·  telemetry aktif"
            )
            self.status.setStyleSheet("color:#168647;font-weight:800")
        else:
            self.status.setText(f"OFFLINE  ·  {self.connection_reason}")
            self.status.setStyleSheet("color:#d7351d;font-weight:800")

    def can_validate_new_work(self) -> bool:
        return self.online and self.device_authorized

    def show_connection_warning(self) -> None:
        self.warning(
            "Start/antrean baru diblokir karena backend belum dapat memvalidasi panel.\n\n"
            + self.connection_reason
        )

    def warning(self, text: str) -> None:
        alert(self, "Perhatian", text, "warning")

    def error(self, text: str) -> None:
        alert(self, "Gagal", text, "error")

    def closeEvent(self, event: Any) -> None:
        self.gpio.close()
        self.store.close()
        super().closeEvent(event)

    @staticmethod
    def styles() -> str:
        return f"""
        QWidget {{ font-family:Arial;font-size:14px;color:#171717; }}
        QMainWindow {{ background:#f7f7f7; }}
        QPushButton {{ min-height:46px;border:0;border-radius:10px;font-weight:800;padding:5px 10px; }}
        #machineTab {{ padding:4px 8px; }}
        #keyboardToggle {{ background:{PURPLE};color:white;font-size:24px; }}
        #keyboardToggle[expanded="true"] {{ background:#9ca3af;color:white; }}
        #machineHeader {{ background:#dddddd;border-radius:10px;min-height:62px; }}
        #heading {{ font-size:23px;font-weight:800;padding-left:10px; }}
        #legendText {{ color:#171717;font-weight:800; }}
        #section {{ background:#dddddd;border-radius:12px; }}
        #sectionTitle {{ font-weight:800;margin-top:2px; }}
        #counterCard {{ background:white;border-radius:11px;min-height:112px; }}
        #counterCaption {{ font-size:22px;font-weight:400; }}
        #counterValue {{ font-size:45px;font-weight:900; }}
        #primaryButton {{ background:{PURPLE};color:white;min-height:58px; }}
        #dangerButton {{ background:{IDLE};color:white;min-height:58px; }}
        #tableAction {{ background:transparent;color:#111;min-height:25px;border-radius:3px; }}
        #closeApp {{ background:{STOP};color:white;font-size:24px; }}
        #keyboardPanel {{ background:#dedede;border-radius:10px; }}
        #keyboardPanel QPushButton {{ background:white;border:1px solid #bdbdbd;min-height:31px;padding:2px 5px; }}
        #connectionStatus {{ min-height:22px; }}
        QLineEdit,QSpinBox {{ background:white;border:1px solid #bdbdbd;border-radius:7px;padding:9px; }}
        QTableWidget {{ background:white;border:1px solid #bbbbbb;border-radius:9px;gridline-color:#c7c7c7;selection-background-color:#e8e5ff;selection-color:#111; }}
        QHeaderView::section {{ background:#eeeeee;border:0;border-right:1px solid #c7c7c7;border-bottom:1px solid #c7c7c7;padding:8px;font-weight:800; }}
        """
