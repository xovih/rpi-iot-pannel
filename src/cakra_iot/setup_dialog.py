from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .api import ApiClient, ApiError
from .dialogs import alert, confirm
from .config import merge_counters, template_counters, write_config


class SetupDialog(QDialog):
    """Wizard pertama kali: dibuka bila panel dijalankan (klik-ganda) tanpa
    config.json. Teknisi cukup mengisi alamat backend, kode gateway, dan device
    token dari menu Gateway / Panel IoT; mesin dibaca dari backend."""

    def __init__(self, target: Path, defaults: dict[str, Any] | None = None) -> None:
        super().__init__()
        defaults = defaults or {}
        self.target = target
        self.saved: dict[str, Any] | None = None
        self.setWindowTitle("CAKRA · Pengaturan Awal Panel IoT")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Panel belum memiliki config.json. Isi data gateway dari menu "
            "<b>Gateway / Panel IoT</b> di web CAKRA. Daftar mesin dibaca otomatis "
            "dari backend; pin sensor counter dapat ditambahkan kemudian pada "
            f"<code>{target}</code>."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.api_url = QLineEdit(defaults.get("api_base_url", "http://192.168.196.196:4000"))
        self.gateway_code = QLineEdit(defaults.get("gateway_code", ""))
        self.gateway_code.setPlaceholderText("mis. RPI-INT-01-TEST")
        self.device_token = QLineEdit(defaults.get("device_token", ""))
        self.device_token.setPlaceholderText("gw_live_…")
        self.device_token.setEchoMode(QLineEdit.Password)
        self.gpio_mode = QComboBox()
        self.gpio_mode.addItems(["real", "mock"])
        self.gpio_mode.setCurrentText(defaults.get("gpio_mode", "real"))
        form.addRow("Alamat backend", self.api_url)
        form.addRow("Kode gateway", self.gateway_code)
        form.addRow("Device token", self.device_token)
        form.addRow("Mode GPIO", self.gpio_mode)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self) -> None:
        raw: dict[str, Any] = {
            "api_base_url": self.api_url.text().strip().rstrip("/"),
            "device_token": self.device_token.text().strip(),
            "gateway_code": self.gateway_code.text().strip().upper(),
            "gpio_mode": self.gpio_mode.currentText(),
            "telemetry_hz": 1,
            "heartbeat_seconds": 30,
            "idle_after_seconds": 15,
            "fullscreen": True,
            "database_path": "data/panel.db",
            "recovery_csv_path": "data/recovery.csv",
            "counters": [],
        }
        if not raw["api_base_url"] or not raw["device_token"]:
            alert(self, "Belum lengkap", "Alamat backend dan device token wajib diisi.", "warning")
            return
        try:
            bootstrap = ApiClient(raw["api_base_url"], raw["device_token"]).bootstrap()
        except ApiError as exc:
            if not confirm(
                self,
                "Backend belum memvalidasi",
                f"{exc} Tetap simpan pengaturan ini?",
                yes="SIMPAN",
            ):
                return
        else:
            raw["gateway_code"] = raw["gateway_code"] or str(bootstrap.get("code", ""))
            # Pin per mesin diambil dari templat config.example.json; DAL tanpa
            # templat dibiarkan kosong untuk diisi teknisi.
            raw["counters"] = merge_counters(
                [machine["dalCode"] for machine in bootstrap.get("machines", [])],
                [],
                template_counters(),
            )
        write_config(self.target, raw)
        self.saved = raw
        self.accept()
