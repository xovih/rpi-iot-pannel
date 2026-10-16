from __future__ import annotations

import time
from collections.abc import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from .pins import PinPlan


class PulseBridge(QObject):
    pulse = pyqtSignal(str)


class GpioManager:
    """Membaca pin sensor counter sesuai `PinPlan` (backend, cache, atau
    config). `apply()` memasang ulang seluruh pin tanpa restart aplikasi,
    sehingga perubahan pin dari menu Gateway berlaku ±10 detik kemudian."""

    def __init__(self, mode: str, on_pulse: Callable[[str], None]) -> None:
        self.mode = mode
        self.plan: PinPlan | None = None
        self.bridge = PulseBridge()
        self.bridge.pulse.connect(on_pulse)
        self._gpio = None

    def apply(self, plan: PinPlan) -> None:
        if self.plan is not None and self.plan.signature == plan.signature:
            return
        self.release()
        self.plan = plan
        if self.mode != "real":
            return
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Mode GPIO real dipilih tetapi driver tidak tersedia "
                "(sudo apt install -y python3-rpi-lgpio)."
            ) from exc
        self._gpio = GPIO
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        pull = {"up": GPIO.PUD_UP, "down": GPIO.PUD_DOWN, "off": GPIO.PUD_OFF}[plan.pull]
        edge = {"falling": GPIO.FALLING, "rising": GPIO.RISING, "both": GPIO.BOTH}[plan.edge]
        for slot in plan.all_slots:
            for pin in slot.pins:
                GPIO.setup(pin, GPIO.IN, pull_up_down=pull)
                GPIO.add_event_detect(
                    pin,
                    edge,
                    callback=lambda _pin, code=slot.code: self.bridge.pulse.emit(code),
                    bouncetime=plan.bounce_ms,
                )

    def simulate(self, slot_code: str) -> None:
        if self.mode == "mock":
            self.bridge.pulse.emit(slot_code)

    def release(self) -> None:
        if self._gpio is not None:
            self._gpio.cleanup()
            self._gpio = None
            time.sleep(0.05)

    def close(self) -> None:
        self.release()
