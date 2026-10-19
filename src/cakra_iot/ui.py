from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PyQt5.QtCore import QSize, QTimer, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
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
from .dialogs import CountdownDialog, alert, confirm
from .icons import play_icon, stop_icon, trash_icon
from .keyboard import OnScreenKeyboard
from .panel_password import Gatekeeper
from .gpio import GpioManager
from .pins import CACHE_KEY, PinPlan, from_bootstrap, from_cache, from_config
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
RECOVERY_BANNER = "DATA DIPULIHKAN · counter dijeda. START = lanjut, lalu FINISH = simpan."
# Mockup hanya memiliki empat status visual. Kondisi koneksi offline tetap
# dijelaskan pada footer, sedangkan tombol DAL memakai warna STOP.
OFFLINE = STOP


def number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


IDLE_KEY = "idle_after_seconds"


def idle_threshold(value: Any, fallback: int) -> int:
    """Ambang IDLE yang sah (detik). Nilai backend/SQLite yang rusak diabaikan."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return fallback
    return seconds if 3 <= seconds <= 3600 else fallback


@dataclass(frozen=True)
class PanelMachine:
    """Satu tab mesin. Identitasnya **selalu** dari backend (revisi 16 Okt
    2026): kode mesin, DAL, nama, dan work center dibaca dari bootstrap sesuai
    mesin yang dinaungi gateway pada menu Gateway / Panel IoT. Pin counter
    per spur juga dari backend (menu Gateway → Pin GPIO); config lokal hanya
    cadangan untuk panel yang pinnya belum diisi di backend."""

    code: str
    dal: str
    name: str
    work_centers: tuple[str, ...]
    slots: tuple[CounterSlot, ...]


class FinishDialog(QDialog):
    """Konfirmasi Finish (revisi 16 Okt 2026).

    Hasil baik dari counter dibatasi sisa qty peti pada tahapan ini; backend
    menolak hasil kumulatif yang melampaui qty peti. Operator memilih apakah
    operator terinput dan peti terproses dihapus dari panel setelah Finish.
    """

    def __init__(
        self,
        counter_qty: int,
        lot_qty: int,
        processed_qty: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Konfirmasi Finish")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("finishDialog")
        self.setFixedWidth(640)
        self.remaining = max(0, lot_qty - processed_qty)
        self.yield_qty = min(counter_qty, self.remaining)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(14)
        layout.addWidget(QLabel("KONFIRMASI FINISH", objectName="finishTitle"))

        facts = QGridLayout()
        facts.setHorizontalSpacing(12)
        for column, (label, value) in enumerate(
            [
                ("QTY LOT", number(lot_qty)),
                # Sama dengan TOTAL PENGERJAAN di Monitoring.
                ("SUDAH TERPROSES", number(processed_qty + self.yield_qty)),
                ("HASIL BAIK SESI INI", number(self.yield_qty)),
            ]
        ):
            caption = QLabel(label, objectName="finishCaption", alignment=Qt.AlignCenter)
            figure = QLabel(value, objectName="finishFigure", alignment=Qt.AlignCenter)
            facts.addWidget(caption, 0, column)
            facts.addWidget(figure, 1, column)
        layout.addLayout(facts)
        if counter_qty > self.yield_qty:
            note = QLabel(
                f"Counter membaca {number(counter_qty)}; hasil baik dibatasi sisa qty lot "
                f"{number(self.remaining)}.",
                objectName="finishNote",
            )
            note.setWordWrap(True)
            layout.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.reject_qty = QSpinBox()
        self.reject_qty.setRange(0, max(0, self.remaining - self.yield_qty))
        self.reject_qty.valueChanged.connect(self.refresh_options)
        self.obstacle = QLineEdit()
        self.obstacle.setPlaceholderText("Opsional — pilih/tulis kendala PMS")
        form.addRow("Jumlah reject", self.reject_qty)
        form.addRow("Kendala", self.obstacle)
        layout.addLayout(form)

        self.clear_operators = QCheckBox("Hapus operator terinput setelah Finish")
        self.clear_operators.setChecked(True)
        self.remove_lot = QCheckBox("Hapus peti terproses dari antrean")
        self.remove_lot.setChecked(True)
        self.lot_note = QLabel(objectName="finishNote")
        self.lot_note.setWordWrap(True)
        layout.addWidget(self.clear_operators)
        layout.addWidget(self.remove_lot)
        layout.addWidget(self.lot_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("BATAL", objectName="finishSecondary")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("FINISH", objectName="finishPrimary")
        confirm.clicked.connect(self.accept)
        for button in (cancel, confirm):
            button.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setStyleSheet(
            f"""
            #finishDialog {{ background:white;border:2px solid {PURPLE};border-radius:14px; }}
            #finishTitle {{ font-size:20px;font-weight:800; }}
            #finishCaption {{ font-size:12px;font-weight:800;color:#525252; }}
            #finishFigure {{ font-size:30px;font-weight:900; }}
            #finishNote {{ color:#b45309;font-size:13px; }}
            QLabel {{ font-size:15px; }}
            QLineEdit,QSpinBox {{ background:white;border:1px solid #bdbdbd;border-radius:7px;
                                 padding:9px;font-size:16px;min-height:26px; }}
            QSpinBox::up-button,QSpinBox::down-button {{ width:34px; }}
            QCheckBox {{ font-size:15px;spacing:10px; }}
            QCheckBox::indicator {{ width:26px;height:26px; }}
            QPushButton {{ min-height:52px;min-width:150px;border:0;border-radius:10px;font-size:16px;font-weight:800; }}
            #finishPrimary {{ background:{PURPLE};color:white; }}
            #finishSecondary {{ background:#e5e5e5;color:#171717; }}
            """
        )
        self.refresh_options()

    @property
    def completes_lot(self) -> bool:
        return self.yield_qty + self.reject_qty.value() >= self.remaining

    def refresh_options(self) -> None:
        # Peti yang qty-nya terpenuhi selalu keluar dari antrean; pilihan
        # menahan peti hanya berlaku bila masih ada sisa.
        if self.completes_lot:
            self.remove_lot.setChecked(True)
            self.remove_lot.setEnabled(False)
            self.lot_note.setText("Qty lot terpenuhi: peti otomatis keluar dari antrean.")
        else:
            self.remove_lot.setEnabled(True)
            left = self.remaining - self.yield_qty - self.reject_qty.value()
            self.lot_note.setText(
                f"Sisa {number(left)} belum terproses. Hilangkan centang untuk menahan peti "
                "di antrean dan melanjutkannya nanti."
            )

    @property
    def keep_in_queue(self) -> bool:
        return not self.completes_lot and not self.remove_lot.isChecked()


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


class MachinePage(QWidget):
    def __init__(self, machine: PanelMachine, host: "PanelWindow") -> None:
        super().__init__()
        self.machine = machine
        self.host = host
        self.queue: list[dict[str, Any]] = []
        # Pergantian peti otomatis (revisi 19 Okt 2026): hitungan per spur saat
        # TOTAL PENGERJAAN tepat mencapai qty peti. Pulsa sesudahnya milik peti
        # berikutnya.
        self.full_queue_id: str | None = None
        self.full_snapshot: dict[str, int] = {}
        self.changeover_declined: set[str] = set()
        # Operator terinput dipulihkan dari SQLite: setelah listrik padam
        # operator tidak perlu memindai ulang NPP-nya.
        self.operators: list[dict[str, str]] = list(
            host.store.get_state(f"operators:{machine.code}", []) or []
        )
        # Sesi berjalan terakhir tersimpan di SQLite: Monitoring langsung terisi
        # saat panel dinyalakan ulang, sebelum backend terjangkau.
        snapshot = host.running_sessions().get(machine.code)
        self.queue = [snapshot] if snapshot else []
        # Setelah exit tak terduga counter mesin ini dijeda sampai START.
        self.recovered = machine.code in host.recovered_machines()

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

        # Di kolom kiri, tepat di atas START, agar kartu counter tidak menciut.
        self.recovery_banner = QLabel(RECOVERY_BANNER, objectName="recoveryBanner")
        self.recovery_banner.setWordWrap(True)
        self.recovery_banner.hide()
        left_layout.addWidget(self.recovery_banner)

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
        self.render_operators()
        if self.queue:
            self.render_queue()

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
        running = next((row for row in self.queue if row["status"] == "RUNNING"), None)
        self.host.remember_session(self.machine.code, running)
        if self.recovered and running is None:
            # Sesi sudah diakhiri (mis. Finish dari outbox atau web).
            self.set_recovered(False)
        team = ((running or {}).get("confirmation") or {}).get("operators") or []
        if running and team and not self.operators:
            # Tim sesi berjalan tercatat di backend; dipakai bila state lokal
            # kosong (mis. panel dipasang ulang di Raspberry Pi lain).
            self.operators = [{"npp": item["npp"], "name": item["name"]} for item in team]
            self.render_operators()
        if (
            running
            and not self.recovered
            and self.host.store.get_state(f"session:{self.machine.code}")
            != self.session_token(str(running["id"]))
        ):
            # Sesi berjalan tanpa baseline lokal (START di perangkat/versi lain,
            # atau database panel baru): hitungan sesi dimulai dari sekarang,
            # bukan terkunci di 0 selamanya.
            self.begin_session(str(running["id"]))
        self.queue_table.setRowCount(len(self.queue))
        select_row = 0
        for row_index, row in enumerate(self.queue):
            lot = row["lotPeti"]
            status = "DIANTRIKAN"
            if row["status"] == "RUNNING":
                status = "DIJEDA" if self.recovered else "DIKERJAKAN"
            self.queue_table.setItem(row_index, 0, QTableWidgetItem(lot["petiId"]))
            self.queue_table.setItem(row_index, 1, QTableWidgetItem(status))
            if row["status"] == "RUNNING" and self.recovered:
                actions = [
                    (play_icon(), "Jalankan ulang counter", lambda _c=False, i=row_index: self.start_row(i))
                ]
            elif row["status"] == "RUNNING":
                actions = [(stop_icon(), "Finish / stop", lambda _c=False, i=row_index: self.finish_row(i))]
            else:
                actions = [
                    (play_icon(), "Start", lambda _c=False, i=row_index: self.start_row(i)),
                    (trash_icon(), "Hapus dari antrean", lambda _c=False, i=row_index: self.delete_row(i)),
                ]
            self.queue_table.setCellWidget(row_index, 2, self.action_cell(actions))
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
            owner = str(row.get("machine", {}).get("code") or self.machine.code).upper()
            if row.get("resumed"):
                # Peti masih aktif di panel ini (mis. setelah listrik padam):
                # buka tab mesinnya dan lanjutkan dari state terakhir.
                page = self.host.focus_queue(owner, str(row["id"]))
                dal = page.machine.dal if page else owner
                state = "dikerjakan" if row.get("status") == "RUNNING" else "mengantre"
                self.host.info(
                    "Sesi dilanjutkan",
                    f"Peti {peti} masih {state} di mesin {dal}. Panel kembali ke sesi "
                    "terakhirnya; counter dan operator dipulihkan.",
                )
                return
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
        self.host.store.set_state(f"operators:{self.machine.code}", self.operators)
        self.operator_table.setRowCount(len(self.operators))
        for row, operator in enumerate(self.operators):
            self.operator_table.setItem(row, 0, QTableWidgetItem(operator["npp"]))
            self.operator_table.setItem(row, 1, QTableWidgetItem(operator["name"]))
            self.operator_table.setCellWidget(
                row,
                2,
                self.action_cell(
                    [(trash_icon(), "Hapus operator", lambda _c=False, i=row: self.remove_operator(i))]
                ),
            )

    def remove_operator(self, row: int) -> None:
        if 0 <= row < len(self.operators):
            self.operators.pop(row)
            self.render_operators()

    @staticmethod
    def action_cell(actions: list[tuple[Any, str, Callable[..., None]]]) -> QWidget:
        """Tombol ikon di tengah sel (vertikal & horizontal)."""
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)
        for icon, tip, handler in actions:
            button = QPushButton()
            button.setObjectName("tableAction")
            button.setIcon(icon)
            button.setIconSize(QSize(22, 22))
            button.setFixedSize(38, 32)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(handler)
            layout.addWidget(button)
        return cell

    def focus_row(self, index: int) -> dict[str, Any] | None:
        if not 0 <= index < len(self.queue):
            return None
        self.queue_table.selectRow(index)
        self.select_queue(index)
        return self.queue[index]

    def start_row(self, index: int) -> None:
        if self.focus_row(index) is not None:
            self.start_or_finish()

    def finish_row(self, index: int) -> None:
        row = self.focus_row(index)
        if row is not None:
            self.finish(row)

    def delete_row(self, index: int) -> None:
        row = self.focus_row(index)
        if row is not None:
            self.delete_queue(row)

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
            if self.recovered:
                # Sesi pulihan: lanjutkan counter dari nilai tersimpan, tanpa
                # START baru di backend (sesi di backend masih berjalan).
                self.set_recovered(False)
                self.render_queue(str(current["id"]))
                return
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
            self.begin_session(str(current["id"]))
            self.reload_queue()
        except ApiError as exc:
            self.host.handle_api_error(exc, dialog=True)

    def finish(self, current: dict[str, Any]) -> None:
        # Angka dialog = angka Monitoring saat FINISH ditekan: Hasil baik sesi ini
        # = HASIL SAAT INI (dikirim sebagai yield_qty), Sudah terproses =
        # TOTAL PENGERJAAN.
        yield_qty = self.current_output()
        lot_qty = int(current.get("lotQty") or current["lotPeti"].get("qty") or 0)
        processed = int(current.get("processedQty") or 0)
        dialog = FinishDialog(yield_qty, lot_qty, processed, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        payload = {
            "queue_id": current["id"],
            "event_id": str(uuid.uuid4()),
            "yield_qty": dialog.yield_qty,
            "scrap_qty": dialog.reject_qty.value(),
            "reason": dialog.obstacle.text().strip() or None,
            "keep_in_queue": dialog.keep_in_queue,
        }
        if dialog.clear_operators.isChecked():
            self.operators.clear()
            self.render_operators()
        if self.host.online and self.host.device_authorized:
            try:
                self.host.api.finish(**payload)
                self.reload_queue()
                return
            except ApiError as exc:
                if exc.status_code is not None:
                    self.host.handle_api_error(exc, dialog=True)
                    return
                self.host.handle_api_error(exc)
        self.host.store.enqueue(payload["event_id"], "finish", payload)
        # Sesi dianggap selesai secara lokal agar tidak di-Finish dua kali bila
        # panel mati sebelum outbox terkirim.
        self.queue = [row for row in self.queue if row["id"] != current["id"]]
        self.render_queue()
        self.host.warning(
            "Jaringan terputus. Finish aman di SQLite outbox dan akan dikirim ulang."
        )

    # ── Pergantian peti otomatis (revisi 19 Okt 2026) ──────────────────────

    CHANGEOVER_SECONDS = 10

    @staticmethod
    def lot_numbers(row: dict[str, Any]) -> tuple[int, int]:
        """(qty peti, sudah terproses sebelum sesi ini) untuk tahapan ini."""
        lot_qty = int(row.get("lotQty") or (row.get("lotPeti") or {}).get("qty") or 0)
        return lot_qty, int(row.get("processedQty") or 0)

    def check_full(self) -> None:
        """Dipanggil tiap pulsa. Saat TOTAL PENGERJAAN tepat mencapai qty peti,
        hitungan per spur dibekukan dan popup hitung mundur dijadwalkan."""
        row = self.running_row()
        if row is None or self.recovered:
            return
        queue_id = str(row["id"])
        if queue_id == self.full_queue_id or queue_id in self.changeover_declined:
            return
        lot_qty, processed = self.lot_numbers(row)
        if lot_qty <= 0 or processed + self.current_output() < lot_qty:
            return
        self.full_queue_id = queue_id
        self.full_snapshot = {
            slot.code: self.host.store.session_counter(slot.code) for slot in self.machine.slots
        }
        QTimer.singleShot(0, self.run_changeover)

    def next_queued(self, after_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in self.queue if row["status"] != "RUNNING" and str(row["id"]) != after_id),
            None,
        )

    def run_changeover(self) -> None:
        row = self.running_row()
        if row is None or str(row["id"]) != self.full_queue_id:
            self.full_queue_id = None
            return
        queue_id = str(row["id"])
        lot_qty, processed = self.lot_numbers(row)
        peti = row["lotPeti"]["petiId"]
        following = self.next_queued(queue_id)
        online = self.host.online and self.host.device_authorized
        if following and online:
            plan = (
                f"Peti {peti} sudah penuh ({number(lot_qty)}/{number(lot_qty)}). Peti ini di-FINISH "
                f"otomatis lalu peti berikutnya {following['lotPeti']['petiId']} dimulai dengan "
                "operator yang sama. Pulsa yang masuk sejak peti penuh dihitung untuk peti berikutnya."
            )
        elif following:
            plan = (
                f"Peti {peti} sudah penuh. Jaringan terputus: FINISH disimpan di outbox, peti "
                f"berikutnya {following['lotPeti']['petiId']} di-START manual setelah online."
            )
        else:
            plan = (
                f"Peti {peti} sudah penuh ({number(lot_qty)}/{number(lot_qty)}) dan tidak ada peti "
                "lain di antrean. Peti di-FINISH otomatis; scan peti berikutnya untuk melanjutkan."
            )
        dialog = CountdownDialog(
            self,
            "Peti penuh — ganti peti",
            plan + " Tekan TAHAN untuk menyelesaikannya sendiri.",
            self.CHANGEOVER_SECONDS,
            "LANJUT",
            "TAHAN",
        )
        dialog.exec_()
        if not dialog.proceeded:
            # Operator memilih menyelesaikan sendiri lewat FINISH; jangan
            # tanyakan lagi untuk peti ini.
            self.changeover_declined.add(queue_id)
            self.full_queue_id = None
            return
        self.auto_finish(row, lot_qty, processed, following)

    def auto_finish(
        self,
        row: dict[str, Any],
        lot_qty: int,
        processed: int,
        following: dict[str, Any] | None,
    ) -> None:
        snapshot = dict(self.full_snapshot)
        yield_qty = min(sum(snapshot.values()), max(0, lot_qty - processed))
        # Pulsa yang masuk selama popup tampil → peti berikutnya.
        carry = {
            code: max(0, self.host.store.session_counter(code) - snapshot.get(code, 0))
            for code in snapshot
        }
        payload = {
            "queue_id": row["id"],
            "event_id": str(uuid.uuid4()),
            "yield_qty": yield_qty,
            "scrap_qty": 0,
            "reason": None,
            "keep_in_queue": False,
        }
        self.full_queue_id = None
        self.full_snapshot = {}
        online = self.host.online and self.host.device_authorized
        finished_online = False
        if online:
            try:
                self.host.api.finish(**payload)
                finished_online = True
            except ApiError as exc:
                if exc.status_code is not None:
                    self.host.handle_api_error(exc, dialog=True)
                    self.reload_queue()
                    return
                self.host.handle_api_error(exc)
        if not finished_online:
            self.host.store.enqueue(payload["event_id"], "finish", payload)
            self.queue = [item for item in self.queue if item["id"] != row["id"]]
            self.render_queue()
            self.host.warning(
                "Jaringan terputus. Finish peti penuh aman di SQLite outbox; START peti "
                "berikutnya setelah panel online."
            )
            return
        if following and self.operators:
            try:
                self.host.api.start(following["id"], [item["npp"] for item in self.operators])
                self.host.store.start_session([slot.code for slot in self.machine.slots], carry)
                self.host.store.set_state(
                    f"session:{self.machine.code}", self.session_token(str(following["id"]))
                )
            except ApiError as exc:
                self.host.handle_api_error(exc, dialog=True)
        elif following:
            self.host.warning("Peti berikutnya belum dimulai: belum ada operator terinput.")
        self.reload_queue()

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
        running = bool(row and row["status"] == "RUNNING")
        self.action.setText("FINISH" if running and not self.recovered else "START")
        self.recovery_banner.setVisible(self.recovered and self.running_row() is not None)

    def set_recovered(self, value: bool) -> None:
        self.recovered = value
        self.host.set_recovered(self.machine.code, value)
        self.refresh_action()

    def begin_session(self, queue_id: str) -> None:
        """Baseline counter sesi dicatat di SQLite dan diikat ke ID antrean,
        sehingga sesi yang dilanjutkan setelah listrik padam tetap menghitung
        dari baseline yang sama."""
        self.host.store.start_session([slot.code for slot in self.machine.slots])
        self.host.store.set_state(f"session:{self.machine.code}", self.session_token(queue_id))

    def session_token(self, queue_id: str) -> str:
        # Pin/spur yang berubah di tengah sesi (mis. Spur 2 dinonaktifkan)
        # memerlukan baseline baru untuk kode counter yang baru.
        return f"{queue_id}|{','.join(slot.code for slot in self.machine.slots)}"

    def running_row(self) -> dict[str, Any] | None:
        return next((row for row in self.queue if row["status"] == "RUNNING"), None)

    def spur_slots(self) -> list[CounterSlot | None]:
        """Posisi kartu Spur 1 dan Spur 2. Spur yang tidak aktif/tidak ada → None."""
        positions: list[CounterSlot | None] = [None, None]
        for index, slot in enumerate(self.machine.slots[:2]):
            position = (slot.slot_no or index + 1) - 1
            if 0 <= position < 2:
                positions[position] = slot
        return positions

    def spur_counts(self) -> list[int | None]:
        """Hitungan sesi per spur (revisi 16 Okt 2026). Mesin satu spur hanya
        memiliki Spur 1; spur yang tidak ada bernilai None dan dihitung 0."""
        running = self.running_row() is not None
        return [
            None if slot is None else (self.host.store.session_counter(slot.code) if running else 0)
            for slot in self.spur_slots()
        ]

    def slot_session_count(self, slot: CounterSlot) -> int:
        return self.host.store.session_counter(slot.code) if self.running_row() else 0

    def current_output(self) -> int:
        """HASIL SAAT INI = Spur 1 + Spur 2 (0 bila mesin satu spur)."""
        return sum(count or 0 for count in self.spur_counts())

    def total_output(self) -> int:
        """TOTAL PENGERJAAN = akumulasi pengerjaan peti pada tahapan/work center
        ini dari sesi-sesi sebelumnya (backend) + HASIL SAAT INI."""
        row = self.running_row() or self.current_queue()
        processed = int((row or {}).get("processedQty") or 0)
        return processed + self.current_output()

    def refresh_counters(self) -> None:
        counts = self.spur_counts()
        self.current_card.value.setText(number(self.current_output()))
        self.total_card.value.setText(number(self.total_output()))
        for card, count in zip(self.slot_cards, counts):
            card.value.setText("—" if count is None else number(count))


class PanelWindow(QMainWindow):
    def __init__(self, config: AppConfig, api: ApiClient, store: LocalStore) -> None:
        super().__init__()
        self.config, self.api, self.store = config, api, store
        self.online = False
        # Ambang IDLE: nilai terakhir dari backend (tersimpan di SQLite agar
        # tetap berlaku saat offline), cadangan config.json.
        self.idle_after_seconds = idle_threshold(
            store.get_state(IDLE_KEY), config.idle_after_seconds
        )
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
        footer = QHBoxLayout()
        footer.setSpacing(12)
        footer.addWidget(self.status, 1)
        # Pengaturan teknisi (alamat backend, token, mode GPIO) dilindungi
        # password; pin GPIO tetap diatur di web.
        self.settings_button = QPushButton("⚙ PENGATURAN")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setToolTip("Pengaturan panel (teknisi, berpassword)")
        self.settings_button.setFocusPolicy(Qt.NoFocus)
        self.settings_button.clicked.connect(self.open_settings)
        footer.addWidget(self.settings_button)
        layout.addLayout(footer)
        self.restart_requested = False
        self.settings_gate = Gatekeeper()
        self.setCentralWidget(root)
        self.setStyleSheet(self.styles())
        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self.remember_input)
        self.update_heading()

        # Penanda proses berjalan di SQLite: bila masih "berjalan" saat panel
        # dibuka, proses sebelumnya berhenti tak terduga (listrik padam, kill).
        self.unexpected_exit = store.begin_run()
        self.paused_slots: set[str] = set()
        if self.unexpected_exit:
            recovered = set(self.recovered_machines()) | set(self.running_sessions())
            self.store.set_state("recovered", sorted(recovered))
        self.gpio = GpioManager(config.gpio_mode, self.on_pulse)
        self.gpio_error: str | None = None
        # Sebelum backend terjangkau, pin diambil dari salinan pemetaan backend
        # terakhir (SQLite) agar panel yang dinyalakan tanpa jaringan tetap
        # menghitung; bila belum pernah ada, dari config.json.
        self.pin_plan: PinPlan = from_cache(store.get_state(CACHE_KEY)) or from_config(config)
        self.install_pins(self.pin_plan)
        # Tab mesin dari bootstrap terakhir: panel yang dinyalakan tanpa
        # jaringan tetap menampilkan mesin, sesi, dan counter tersimpan.
        cached_machines = store.get_state("machines") or []
        if cached_machines:
            self.apply_machines([self.panel_machine(row) for row in cached_machines])
        if self.unexpected_exit:
            QTimer.singleShot(700, self.show_recovery_notice)
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
            idle = idle_threshold(
                (bootstrap.get("status") or {}).get("idleAfterSeconds"), self.idle_after_seconds
            )
            if idle != self.idle_after_seconds or self.store.get_state(IDLE_KEY) != idle:
                self.idle_after_seconds = idle
                self.store.set_state(IDLE_KEY, idle)
            backend_plan = from_bootstrap(bootstrap)
            if backend_plan is not None:
                self.store.set_state(CACHE_KEY, backend_plan.to_cache())
            # Panel yang pinnya belum diisi di backend tetap memakai config.json.
            self.install_pins(backend_plan or from_config(self.config))
            machines = [self.panel_machine(row) for row in bootstrap.get("machines", [])]
            if bootstrap.get("machines") != self.store.get_state("machines"):
                self.store.set_state("machines", bootstrap.get("machines", []))
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

    def install_pins(self, plan: PinPlan) -> None:
        """Memasang (ulang) GPIO bila pemetaan pin berubah — tanpa restart."""
        if plan.signature == getattr(self.gpio.plan, "signature", None) and not self.gpio_error:
            self.pin_plan = plan
            return
        self.pin_plan = plan
        try:
            self.gpio.apply(plan)
            self.gpio_error = None
        except Exception as exc:  # noqa: BLE001 — galat driver GPIO beragam
            # Panel tetap terbuka untuk antrean/operator, tetapi counter mati;
            # teknisi harus tahu sebabnya tanpa membuka terminal.
            first = self.gpio_error is None
            self.gpio_error = str(exc) or exc.__class__.__name__
            if first:
                QTimer.singleShot(
                    500,
                    lambda: alert(
                        self,
                        "GPIO tidak aktif",
                        f"Counter sensor tidak terbaca: {self.gpio_error}. Jalankan "
                        "bash gpio-test.sh untuk diagnosis (lihat panduan pemasangan).",
                    ),
                )

    def panel_machine(self, row: dict[str, Any]) -> PanelMachine:
        dal = str(row["dalCode"]).strip().upper()
        return PanelMachine(
            code=str(row["code"]).strip().upper(),
            dal=dal,
            name=str(row.get("name") or dal),
            work_centers=tuple(
                str(item["code"]) for item in row.get("workCenters", []) if item.get("code")
            ),
            slots=self.pin_plan.slots_for(dal),
        )

    def apply_machines(self, machines: list[PanelMachine]) -> None:
        """Membangun ulang tab hanya bila susunan mesin backend berubah, agar
        antrean dan operator yang sedang dipindai tidak hilang tiap 10 detik."""
        if [page.machine for page in self.pages] == machines:
            return
        current_code = (
            self.current_page().machine.code
            if self.current_page()
            else self.store.get_state("selected_machine")
        )
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
        self.update_paused_slots()

    # ── Pemulihan setelah exit tak terduga ─────────────────────────────────

    def running_sessions(self) -> dict[str, dict[str, Any]]:
        return dict(self.store.get_state("sessions", {}) or {})

    def remember_session(self, machine_code: str, row: dict[str, Any] | None) -> None:
        sessions = self.running_sessions()
        if sessions.get(machine_code) == row:
            return
        if row is None:
            sessions.pop(machine_code, None)
        else:
            sessions[machine_code] = row
        self.store.set_state("sessions", sessions)

    def recovered_machines(self) -> list[str]:
        return list(self.store.get_state("recovered", []) or [])

    def set_recovered(self, machine_code: str, value: bool) -> None:
        recovered = set(self.recovered_machines())
        if value:
            recovered.add(machine_code)
        else:
            recovered.discard(machine_code)
        self.store.set_state("recovered", sorted(recovered))
        self.update_paused_slots()

    def update_paused_slots(self) -> None:
        # Pulsa mesin yang dijeda tidak dihitung sampai operator menekan START.
        self.paused_slots = {
            slot.code for page in self.pages if page.recovered for slot in page.machine.slots
        }

    def recovery_message(self) -> str | None:
        lines = []
        for page in self.pages:
            row = page.running_row()
            if page.recovered and row:
                lines.append(
                    f"• {page.machine.dal} · peti {row['lotPeti']['petiId']} · "
                    f"HASIL SAAT INI {number(page.current_output())}"
                )
        pending = len(self.store.pending(1000))
        if not lines and not pending:
            return None
        text = (
            "Panel terakhir kali mengalami exit yang tak terduga (mis. listrik padam "
            "atau ditutup paksa)."
        )
        if lines:
            text += (
                " Data counter dipulihkan dari penyimpanan lokal:\n"
                + "\n".join(lines)
                + "\n\nCounter perlu di-START ulang untuk berjalan kembali. Setelah START, "
                "Anda bisa menekan FINISH (stop) untuk menyimpan data ke server."
            )
        if pending:
            text += (
                f"\n\n{pending} data yang belum terkirim tersimpan aman dan dikirim otomatis "
                "saat backend terhubung."
            )
        return text

    def show_recovery_notice(self) -> None:
        text = self.recovery_message()
        if text:
            alert(self, "Data dipulihkan", text, "warning")

    def current_page(self) -> MachinePage | None:
        index = self.stack.currentIndex()
        return self.pages[index] if 0 <= index < len(self.pages) else None

    def select_machine(self, index: int) -> None:
        if not 0 <= index < len(self.pages):
            return
        self.stack.setCurrentIndex(index)
        self.store.set_state("selected_machine", self.pages[index].machine.code)
        self.update_heading()
        self.refresh_tabs()
        self.pages[index].reload_queue()

    def focus_queue(self, machine_code: str, queue_id: str) -> MachinePage | None:
        index = next(
            (i for i, page in enumerate(self.pages) if page.machine.code == machine_code), None
        )
        if index is None:
            return None
        self.select_machine(index)
        self.pages[index].select_queue_by_id(queue_id)
        return self.pages[index]

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

    def open_settings(self) -> None:
        from .settings_dialog import PasswordDialog, SettingsDialog

        if PasswordDialog(self, self.config, self.settings_gate).exec_() != QDialog.Accepted:
            return
        if SettingsDialog(self, self.config).exec_() != QDialog.Accepted:
            return
        # Alamat backend & token dipakai seluruh koneksi dan GPIO dipegang
        # proses ini: cara paling aman menerapkannya adalah mulai ulang.
        self.restart_requested = True
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
        if slot_code in self.paused_slots:
            return
        self.store.increment(slot_code)
        # Peti penuh → pergantian peti otomatis (revisi 19 Okt 2026).
        for page in self.pages:
            if any(slot.code == slot_code for slot in page.machine.slots):
                page.check_full()

    def refresh_ui(self) -> None:
        for page in self.pages:
            page.refresh_counters()
        self.refresh_tabs()

    def machine_state(self, page: MachinePage, now: datetime) -> str:
        if page.recovered or not any(row["status"] == "RUNNING" for row in page.queue):
            return "STOP"
        pulses = [self.store.last_pulse(slot.code) for slot in page.machine.slots]
        recent = any(
            pulse
            and (now - datetime.fromisoformat(pulse)).total_seconds()
            < self.idle_after_seconds
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
                        # HASIL sesi per spur untuk dashboard denah lini
                        # (revisi 17 Okt 2026); 0 bila tidak ada sesi.
                        "sessionCount": page.slot_session_count(slot),
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
                "Terbitkan/rotasi token gateway lalu isi lewat tombol PENGATURAN."
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
            plan = getattr(self, "pin_plan", None)
            source = f" ({plan.source})" if plan else ""
            gpio = (
                "GPIO ERROR"
                if getattr(self, "gpio_error", None)
                else f"GPIO {self.config.gpio_mode}{source}"
            )
            self.status.setText(
                f"ONLINE  ·  {self.config.api_base_url}  ·  {len(self.pages)} mesin  ·  "
                f"{gpio}  ·  telemetry aktif"
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

    def info(self, title: str, text: str) -> None:
        alert(self, title, text, "info")

    def warning(self, text: str) -> None:
        alert(self, "Perhatian", text, "warning")

    def error(self, text: str) -> None:
        alert(self, "Gagal", text, "error")

    def closeEvent(self, event: Any) -> None:
        self.store.end_run()
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
        #tableAction {{ background:transparent;border:0;min-height:0;padding:0;border-radius:6px; }}
        #tableAction:pressed {{ background:#e5e5e5; }}
        #closeApp {{ background:{STOP};color:white;font-size:24px; }}
        #settingsButton {{ background:#e5e5e5;color:#171717;min-height:40px;padding:2px 16px; }}
        #settingsButton:pressed {{ background:#d4d4d4; }}
        #keyboardPanel {{ background:#dedede;border-radius:10px; }}
        #keyboardPanel QPushButton {{ background:white;border:1px solid #bdbdbd;min-height:31px;padding:2px 5px; }}
        #connectionStatus {{ min-height:22px; }}
        #recoveryBanner {{ background:#fff4d6;border:2px solid #f59e0b;border-radius:9px;color:#7a4b00;
                           font-weight:800;font-size:13px;padding:6px 10px; }}
        QLineEdit,QSpinBox {{ background:white;border:1px solid #bdbdbd;border-radius:7px;padding:9px; }}
        QTableWidget {{ background:white;border:1px solid #bbbbbb;border-radius:9px;gridline-color:#c7c7c7;selection-background-color:#e8e5ff;selection-color:#111; }}
        QHeaderView::section {{ background:#eeeeee;border:0;border-right:1px solid #c7c7c7;border-bottom:1px solid #c7c7c7;padding:8px;font-weight:800; }}
        """
