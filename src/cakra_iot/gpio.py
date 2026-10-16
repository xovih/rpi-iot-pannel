from __future__ import annotations

import time
from collections.abc import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from .config import AppConfig, CounterSlot


class PulseBridge(QObject):
    pulse = pyqtSignal(str)


class GpioManager:
    """Membaca pin sensor counter. Pin dipasang dari config lokal saja: kabel
    sensor fisik tetap menghitung walau mesinnya belum dipetakan backend."""

    def __init__(
        self,
        slots: tuple[CounterSlot, ...],
        mode: str,
        on_pulse: Callable[[str], None],
        config: AppConfig | None = None,
    ) -> None:
        self.slots = slots
        self.mode = mode
        self.pull = config.gpio_pull if config else "up"
        self.edge = config.gpio_edge if config else "falling"
        self.bounce_ms = config.gpio_bounce_ms if config else 110
        self.bridge = PulseBridge()
        self.bridge.pulse.connect(on_pulse)
        self._gpio = None

    def start(self) -> None:
        if self.mode != "real":
            return
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Mode GPIO real dipilih tetapi RPi.GPIO tidak tersedia.") from exc
        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        pull = {"up": GPIO.PUD_UP, "down": GPIO.PUD_DOWN, "off": GPIO.PUD_OFF}[self.pull]
        edge = {"falling": GPIO.FALLING, "rising": GPIO.RISING, "both": GPIO.BOTH}[self.edge]
        for slot in self.slots:
            for pin in slot.pins:
                GPIO.setup(pin, GPIO.IN, pull_up_down=pull)
                GPIO.add_event_detect(
                    pin,
                    edge,
                    callback=lambda _pin, code=slot.code: self.bridge.pulse.emit(code),
                    bouncetime=self.bounce_ms,
                )

    def simulate(self, slot_code: str) -> None:
        if self.mode == "mock":
            self.bridge.pulse.emit(slot_code)

    def close(self) -> None:
        if self._gpio is not None:
            self._gpio.cleanup()
            time.sleep(0.05)
