"""Password pengaturan panel (tombol PENGATURAN di kanan bawah).

Disimpan di config.json hanya sebagai hash PBKDF2-SHA256 bergaram, sehingga
membaca config.json tidak membuka password-nya. Panel yang belum memiliki
password menerima device token sebagai password sementara, lalu wajib membuat
password baru — device token hanya diketahui teknisi yang memasang panel.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SCHEME = "pbkdf2_sha256"
ITERATIONS = 120_000
MIN_LENGTH = 6
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{SCHEME}${iterations}${salt}${digest.hex()}"


def verify_password(stored: str, password: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def check_new_password(password: str, repeat: str) -> str | None:
    """Pesan galat bila password baru tidak sah; None bila boleh dipakai."""
    if len(password) < MIN_LENGTH:
        return f"Password pengaturan minimal {MIN_LENGTH} karakter."
    if password != repeat:
        return "Ulangi password tidak sama."
    return None


class Gatekeeper:
    """Memeriksa password dan mengunci sementara setelah salah berulang,
    agar password tidak dapat ditebak dengan mencoba-coba di layar sentuh."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.failures = 0
        self.locked_until = 0.0

    def locked_seconds(self) -> int:
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def check(self, password_hash: str, device_token: str, password: str) -> bool:
        if self.locked_seconds():
            return False
        if password_hash:
            ok = verify_password(password_hash, password)
        else:
            ok = bool(device_token) and hmac.compare_digest(
                password.encode("utf-8"), device_token.encode("utf-8")
            )
        if ok:
            self.failures = 0
            return True
        self.failures += 1
        if self.failures >= MAX_ATTEMPTS:
            self.failures = 0
            self.locked_until = self.clock() + LOCKOUT_SECONDS
        return False
