"""Tombol PENGATURAN panel (kanan bawah): ubah alamat backend, device token,
kode gateway, mode GPIO, dan password pengaturan tanpa membuka terminal.

Pin GPIO sengaja tidak ada di sini — pin dikelola di web (Gateway / Panel IoT
→ Pin GPIO). Perubahan ditulis ke config.json lalu panel dimulai ulang, karena
alamat backend dan token dipakai oleh seluruh koneksi panel.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .api import ApiClient, ApiError
from .config import CONFIG_FILENAME, AppConfig, app_dir, find_config, write_config
from .dialogs import PURPLE, alert, confirm
from .keyboard import OnScreenKeyboard
from .panel_password import MIN_LENGTH, Gatekeeper, check_new_password, hash_password

DANGER = "#ff3b18"

STYLE = f"""
#settingsDialog {{ background:white;border:2px solid {PURPLE};border-radius:14px; }}
#settingsTitle {{ font-size:22px;font-weight:800;color:#171717; }}
#settingsNote {{ font-size:15px;color:#404040; }}
#settingsError {{ font-size:15px;font-weight:800;color:{DANGER}; }}
QLabel {{ font-size:16px; }}
QLineEdit, QComboBox {{ font-size:18px;min-height:44px;background:white;border:1px solid #bdbdbd;
                        border-radius:8px;padding:2px 10px; }}
QLineEdit:focus {{ border:2px solid {PURPLE}; }}
#settingsButtons QPushButton {{ min-height:56px;min-width:170px;border:0;border-radius:10px;
                                font-size:16px;font-weight:800;padding:4px 18px; }}
#dialogPrimary {{ background:{PURPLE};color:white; }}
#dialogSecondary {{ background:#e5e5e5;color:#171717; }}
#dialogTest {{ background:#dcd7ff;color:{PURPLE}; }}
#keyboardPanel {{ background:#dedede;border-radius:10px; }}
#keyboardPanel QPushButton {{ background:white;border:1px solid #bdbdbd;border-radius:8px;min-height:38px;
                              font-size:16px;font-weight:800;padding:2px 5px; }}
#showSecret {{ min-height:44px;min-width:110px;border:0;border-radius:8px;background:#e5e5e5;font-weight:800; }}
"""


def apply_settings(
    raw: dict[str, Any],
    *,
    api_base_url: str,
    gateway_code: str,
    device_token: str,
    gpio_mode: str,
    new_password: str,
) -> dict[str, Any]:
    """Salinan config.json dengan nilai dari dialog. Token kosong = tetap;
    password kosong = tetap. Kunci lain (pin cadangan, path data) dibiarkan."""
    result = dict(raw)
    result["api_base_url"] = api_base_url.strip().rstrip("/")
    result["gateway_code"] = gateway_code.strip().upper()
    result["gpio_mode"] = gpio_mode
    if device_token.strip():
        result["device_token"] = device_token.strip()
    if new_password:
        result["settings_password_hash"] = hash_password(new_password)
    return result


def row_label(text: str) -> QLabel:
    # Label setinggi kolom isian agar teks sejajar tengah dengan kotaknya.
    label = QLabel(text, alignment=Qt.AlignLeft | Qt.AlignVCenter)
    label.setMinimumHeight(48)
    return label


class KioskDialog(QDialog):
    """Dasar dialog tanpa bingkai dengan keyboard layar sentuh lengkap."""

    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("settingsDialog")
        self.setStyleSheet(STYLE)
        self.inputs: list[QLineEdit] = []
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(26, 22, 26, 20)
        self.outer.setSpacing(14)
        self.outer.addWidget(QLabel(title, objectName="settingsTitle"))
        self.error_label = QLabel("", objectName="settingsError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

    def field(self, text: str = "", secret: bool = False) -> QLineEdit:
        edit = QLineEdit(text)
        if secret:
            edit.setEchoMode(QLineEdit.Password)
        self.inputs.append(edit)
        return edit

    def keyboard_target(self) -> QLineEdit | None:
        focused = QApplication.focusWidget()
        if isinstance(focused, QLineEdit) and focused in self.inputs:
            return focused
        return self.inputs[0] if self.inputs else None

    def finish_layout(self, buttons: list[tuple[str, str, Any]]) -> None:
        self.outer.addWidget(self.error_label)
        keyboard = OnScreenKeyboard(self.keyboard_target, full=True)
        keyboard.setFixedHeight(250)
        self.outer.addWidget(keyboard)
        row_widget = QWidget(objectName="settingsButtons")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        for index, (label, name, handler) in enumerate(buttons):
            if index == 1:
                row.addStretch(1)
            button = QPushButton(label, objectName=name)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(handler)
            row.addWidget(button)
        self.outer.addWidget(row_widget)
        if self.inputs:
            self.inputs[0].setFocus()

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))


class PasswordDialog(KioskDialog):
    """Gerbang password sebelum dialog pengaturan dibuka."""

    def __init__(self, parent: QWidget | None, config: AppConfig, gate: Gatekeeper) -> None:
        super().__init__(parent, "Pengaturan Panel")
        self.config, self.gate = config, gate
        self.setFixedWidth(900)
        if config.settings_password_hash:
            note = "Masukkan password pengaturan panel. Hanya untuk teknisi."
        else:
            note = (
                "Password pengaturan belum dibuat. Masukkan <b>device token</b> panel ini "
                "(dari menu Gateway / Panel IoT) sebagai password sementara; setelah itu "
                "Anda diminta membuat password baru."
            )
        label = QLabel(note, objectName="settingsNote")
        label.setWordWrap(True)
        self.outer.addWidget(label)
        self.password = self.field(secret=True)
        self.password.setPlaceholderText("Password")
        self.password.returnPressed.connect(self.submit)
        line = QHBoxLayout()
        line.addWidget(self.password, 1)
        toggle = QPushButton("LIHAT", objectName="showSecret")
        toggle.setFocusPolicy(Qt.NoFocus)
        toggle.clicked.connect(lambda: self.reveal(toggle))
        line.addWidget(toggle)
        self.outer.addLayout(line)
        self.finish_layout(
            [("BATAL", "dialogSecondary", self.reject), ("MASUK", "dialogPrimary", self.submit)]
        )
        if gate.locked_seconds():
            self.show_error(f"Terlalu banyak percobaan. Coba lagi {gate.locked_seconds()} detik lagi.")

    def reveal(self, button: QPushButton) -> None:
        hidden = self.password.echoMode() == QLineEdit.Password
        self.password.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        button.setText("SEMBUNYI" if hidden else "LIHAT")

    def submit(self) -> None:
        if self.gate.check(
            self.config.settings_password_hash, self.config.device_token, self.password.text()
        ):
            self.accept()
            return
        self.password.clear()
        wait = self.gate.locked_seconds()
        self.show_error(
            f"Terlalu banyak percobaan. Coba lagi {wait} detik lagi."
            if wait
            else "Password salah."
        )


class SettingsDialog(KioskDialog):
    def __init__(self, parent: QWidget | None, config: AppConfig) -> None:
        super().__init__(parent, "Pengaturan Panel")
        self.config = config
        self.target = config.source or find_config() or app_dir() / CONFIG_FILENAME
        self.must_set_password = not config.settings_password_hash
        self.setFixedWidth(980)

        note = QLabel(
            "Pin GPIO diatur di web: "
            "<b>Gateway / Panel IoT → Pin GPIO</b>. Setelah disimpan, panel dimulai ulang; "
            "counter dan Finish yang belum terkirim tetap aman.",
            objectName="settingsNote",
        )
        note.setWordWrap(True)
        self.outer.addWidget(note)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        self.api_url = self.field(config.api_base_url)
        self.gateway_code = self.field(config.gateway_code)
        self.device_token = self.field(secret=True)
        self.device_token.setPlaceholderText("kosongkan bila tidak diganti")
        self.gpio_mode = QComboBox()
        self.gpio_mode.addItems(["real", "mock"])
        self.gpio_mode.setCurrentText(config.gpio_mode)
        self.new_password = self.field(secret=True)
        self.repeat_password = self.field(secret=True)
        if self.must_set_password:
            self.new_password.setPlaceholderText(f"wajib dibuat, min. {MIN_LENGTH} karakter")
        else:
            self.new_password.setPlaceholderText("kosongkan bila tidak diganti")
        form.addRow(row_label("Alamat backend"), self.api_url)
        form.addRow(row_label("Kode gateway"), self.gateway_code)
        form.addRow(row_label("Device token baru"), self.device_token)
        form.addRow(row_label("Mode GPIO"), self.gpio_mode)
        form.addRow(row_label("Password pengaturan baru"), self.new_password)
        form.addRow(row_label("Ulangi password"), self.repeat_password)
        self.outer.addLayout(form)
        self.finish_layout(
            [
                ("UJI KONEKSI", "dialogTest", self.test_connection),
                ("BATAL", "dialogSecondary", self.reject),
                ("SIMPAN && MULAI ULANG", "dialogPrimary", self.save),
            ]
        )

    def token(self) -> str:
        return self.device_token.text().strip() or self.config.device_token

    def url(self) -> str:
        return self.api_url.text().strip().rstrip("/")

    def bootstrap(self) -> dict[str, Any]:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return ApiClient(self.url(), self.token()).bootstrap()
        finally:
            QApplication.restoreOverrideCursor()

    def validate(self) -> str | None:
        if not self.url().startswith(("http://", "https://")):
            return "Alamat backend harus diawali http:// atau https://, mis. http://192.168.196.196:4000."
        password = self.new_password.text()
        if password or self.repeat_password.text() or self.must_set_password:
            return check_new_password(password, self.repeat_password.text())
        return None

    def test_connection(self) -> None:
        if not self.url().startswith(("http://", "https://")):
            self.show_error("Alamat backend harus diawali http:// atau https://.")
            return
        self.show_error("")
        try:
            bootstrap = self.bootstrap()
        except ApiError as exc:
            alert(self, "Koneksi gagal", str(exc), "error")
            return
        alert(
            self,
            "Koneksi berhasil",
            f"Token milik gateway {bootstrap.get('code', '-')} dengan "
            f"{len(bootstrap.get('machines', []))} mesin.",
            "info",
        )

    def save(self) -> None:
        problem = self.validate()
        if problem:
            self.show_error(problem)
            return
        try:
            raw = json.loads(Path(self.target).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        updated = apply_settings(
            raw,
            api_base_url=self.url(),
            gateway_code=self.gateway_code.text(),
            device_token=self.device_token.text(),
            gpio_mode=self.gpio_mode.currentText(),
            new_password=self.new_password.text(),
        )
        try:
            AppConfig.from_dict(updated, base_dir=Path(self.target).parent)
        except (ValueError, KeyError) as exc:
            self.show_error(f"Pengaturan tidak sah: {exc}")
            return
        try:
            bootstrap = self.bootstrap()
        except ApiError as exc:
            if not confirm(
                self,
                "Backend belum memvalidasi",
                f"{exc} Tetap simpan dan mulai ulang panel?",
                yes="SIMPAN",
            ):
                return
        else:
            code = str(bootstrap.get("code", "")).upper()
            if code and updated["gateway_code"] != code:
                # Token menentukan gateway; kode lokal hanya label.
                updated["gateway_code"] = code
        write_config(Path(self.target), updated)
        self.accept()
