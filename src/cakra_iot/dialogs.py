from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

PURPLE = "#3212ef"
DANGER = "#ff3b18"
WARNING = "#f59e0b"
NEUTRAL = "#575252"

TONES = {
    "error": (DANGER, "✕"),
    "warning": (WARNING, "!"),
    "question": (PURPLE, "?"),
}


class PanelDialog(QDialog):
    """Dialog kiosk berukuran tetap untuk layar sentuh panel.

    Menggantikan `QMessageBox`, yang tata letaknya dikendalikan gaya bawaan
    OS: pada Raspberry Pi ikonnya ikut melebar dan teks terpotong di tepi
    kanan (laporan USER 16 Okt 2026). Di sini lebar, bungkus teks, dan ukuran
    tombol ditetapkan sendiri sehingga tampilannya sama di Windows dan Pi.
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        tone: str = "error",
        buttons: tuple[str, ...] = ("OK",),
    ) -> None:
        super().__init__(parent)
        self.choice: str | None = None
        color, glyph = TONES.get(tone, TONES["error"])
        self.setWindowTitle(title)
        self.setModal(True)
        # Tanpa bingkai jendela: kiosk tidak memiliki tombol minimize/close.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setFixedWidth(620)
        self.setObjectName("panelDialog")
        self.setStyleSheet(
            f"""
            #panelDialog {{ background:white;border:2px solid {color};border-radius:14px; }}
            #dialogIcon {{ background:{color};color:white;border-radius:24px;font-size:26px;font-weight:900; }}
            #dialogTitle {{ font-size:20px;font-weight:800;color:#171717; }}
            #dialogMessage {{ font-size:16px;color:#262626; }}
            QPushButton {{ min-height:52px;min-width:150px;border:0;border-radius:10px;
                           font-size:16px;font-weight:800;padding:4px 18px; }}
            #dialogPrimary {{ background:{color};color:white; }}
            #dialogSecondary {{ background:#e5e5e5;color:#171717; }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 24, 26, 22)
        outer.setSpacing(18)

        head = QHBoxLayout()
        head.setSpacing(16)
        icon = QLabel(glyph, objectName="dialogIcon", alignment=Qt.AlignCenter)
        icon.setFixedSize(48, 48)
        head.addWidget(icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addWidget(QLabel(title, objectName="dialogTitle"))
        body = QLabel(message, objectName="dialogMessage")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.NoTextInteraction)
        texts.addWidget(body)
        head.addLayout(texts, 1)
        outer.addLayout(head)

        row = QHBoxLayout()
        row.addStretch(1)
        for index, label in enumerate(buttons):
            primary = index == len(buttons) - 1
            button = QPushButton(label, objectName="dialogPrimary" if primary else "dialogSecondary")
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _checked, value=label: self.finish(value))
            row.addWidget(button)
        outer.addLayout(row)

    def finish(self, value: str) -> None:
        self.choice = value
        self.accept()


def alert(parent: QWidget | None, title: str, message: str, tone: str = "error") -> None:
    PanelDialog(parent, title, message, tone).exec_()


def confirm(parent: QWidget | None, title: str, message: str, yes: str = "YA", no: str = "BATAL") -> bool:
    dialog = PanelDialog(parent, title, message, "question", (no, yes))
    dialog.exec_()
    return dialog.choice == yes
