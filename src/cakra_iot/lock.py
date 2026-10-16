from __future__ import annotations

import os
from pathlib import Path
from typing import IO


def single_instance(path: Path) -> IO[str] | None:
    """Kunci berkas milik OS (flock/msvcrt): dilepas otomatis saat proses
    mati — termasuk listrik padam — sehingga panel selalu dapat dibuka lagi.
    Pegangan yang dikembalikan wajib disimpan selama panel berjalan."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle
