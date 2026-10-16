from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

GREEN = "#16a34a"
RED = "#dc2626"

SIZE = 64


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    return pixmap, painter


def play_icon(color: str = GREEN) -> QIcon:
    """Segitiga Start. Digambar sendiri agar tidak bergantung font emoji Pi."""
    pixmap, painter = _canvas()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygonF([QPointF(18, 10), QPointF(54, 32), QPointF(18, 54)]))
    painter.end()
    return QIcon(pixmap)


def stop_icon(color: str = RED) -> QIcon:
    pixmap, painter = _canvas()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(14, 14, 36, 36), 6, 6)
    painter.end()
    return QIcon(pixmap)


def trash_icon(color: str = RED) -> QIcon:
    pixmap, painter = _canvas()
    pen = QPen(QColor(color), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(QPointF(12, 18), QPointF(52, 18))  # tutup
    painter.drawLine(QPointF(26, 18), QPointF(26, 11))
    painter.drawLine(QPointF(26, 11), QPointF(38, 11))
    painter.drawLine(QPointF(38, 11), QPointF(38, 18))
    painter.drawPolygon(QPolygonF([QPointF(17, 24), QPointF(47, 24), QPointF(44, 55), QPointF(20, 55)]))
    painter.drawLine(QPointF(28, 31), QPointF(28, 48))
    painter.drawLine(QPointF(36, 31), QPointF(36, 48))
    painter.end()
    return QIcon(pixmap)
