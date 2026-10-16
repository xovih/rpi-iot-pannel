from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QGridLayout, QLineEdit, QPushButton

ROWS = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM_-."]


class OnScreenKeyboard(QFrame):
    """Keyboard layar sentuh panel.

    Mode baku hanya huruf besar — cukup untuk ID peti dan NPP. Mode `full`
    dipakai dialog PENGATURAN: huruf kecil/besar (tombol `ABC`/`abc`) serta
    `:` dan `/` agar alamat backend dan device token dapat diketik tanpa
    keyboard USB.
    """

    def __init__(self, target_provider: Callable[[], QLineEdit | None], full: bool = False) -> None:
        super().__init__(objectName="keyboardPanel")
        self.target_provider = target_provider
        self.full = full
        self.lower = full
        self.letters: list[tuple[QPushButton, str]] = []
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setHorizontalSpacing(7)
        layout.setVerticalSpacing(6)
        for row, keys in enumerate(ROWS):
            for column, key in enumerate(keys):
                label = "GARIS _" if key == "_" and not full else key
                button = self.key(label, key)
                if key.isalpha():
                    self.letters.append((button, key))
                layout.addWidget(button, row, column)
        if full:
            layout.addWidget(self.key(":", ":"), 2, 9)
            self.case_button = self.key("ABC", "CASE")
            layout.addWidget(self.case_button, 4, 0, 1, 2)
            layout.addWidget(self.key("/", "/"), 4, 2)
            layout.addWidget(self.key("SPASI", "SPACE"), 4, 3, 1, 2)
            layout.addWidget(self.key("BERSIHKAN", "CLEAR"), 4, 5, 1, 2)
            layout.addWidget(self.key("⌫ HAPUS", "BACKSPACE"), 4, 7, 1, 2)
            layout.addWidget(self.key("ENTER", "ENTER"), 4, 9)
            self.render_case()
        else:
            layout.addWidget(self.key("BERSIHKAN", "CLEAR"), 4, 0, 1, 2)
            layout.addWidget(self.key("SPASI", "SPACE"), 4, 2, 1, 4)
            layout.addWidget(self.key("⌫ HAPUS", "BACKSPACE"), 4, 6, 1, 2)
            layout.addWidget(self.key("ENTER", "ENTER"), 4, 8, 1, 2)

    def key(self, label: str, value: str) -> QPushButton:
        button = QPushButton(label)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _checked, content=value: self.insert(content))
        return button

    def render_case(self) -> None:
        for button, letter in self.letters:
            button.setText(letter.lower() if self.lower else letter)
        # Label tombol menunjukkan huruf yang akan didapat setelah ditekan.
        self.case_button.setText("ABC" if self.lower else "abc")

    def insert(self, value: str) -> None:
        if value == "CASE":
            self.lower = not self.lower
            self.render_case()
            return
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
            target.insert(value.lower() if self.lower and value.isalpha() else value)
        target.setFocus()
