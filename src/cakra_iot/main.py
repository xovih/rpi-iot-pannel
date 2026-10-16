from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

from PyQt5.QtWidgets import QApplication, QDialog

from .api import ApiClient
from .dialogs import alert
from .config import CONFIG_FILENAME, AppConfig, app_dir, find_config
from .storage import LocalStore

log = logging.getLogger("cakra_iot")


def setup_logging(folder: Path) -> None:
    """Panel dibuka dengan klik-ganda tanpa terminal, jadi jejaknya ditulis
    ke berkas agar teknisi tetap dapat menelusuri galat."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        folder / "panel.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def single_instance(path: Path) -> IO[str] | None:
    """Kunci berkas milik OS (flock/msvcrt): dilepas otomatis saat proses
    mati — termasuk listrik padam — sehingga panel selalu dapat dibuka lagi.
    Pegangan yang dikembalikan wajib disimpan selama panel berjalan."""
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


def fatal(message: str) -> int:
    log.error(message)
    alert(None, "CAKRA · Panel IoT", message)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Panel kiosk IoT CAKRA")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Lokasi config; tanpa ini dicari {CONFIG_FILENAME} di folder aplikasi",
    )
    parser.add_argument("--windowed", action="store_true", help="Jangan paksa fullscreen")
    parser.add_argument("--setup", action="store_true", help="Buka ulang wizard pengaturan awal")
    args = parser.parse_args()

    application = QApplication(sys.argv)
    application.setApplicationName("CAKRA Panel IoT")

    config_path = find_config(args.config)
    if args.setup or config_path is None:
        from .setup_dialog import SetupDialog

        target = args.config or config_path or app_dir() / CONFIG_FILENAME
        dialog = SetupDialog(target)
        if dialog.exec_() != QDialog.Accepted:
            return 0
        config_path = target

    try:
        config = AppConfig.load(config_path)
    except (OSError, ValueError, KeyError) as exc:
        return fatal(f"config.json tidak dapat dibaca ({config_path}):\n{exc}")

    data_dir = config.database_path.parent
    setup_logging(data_dir)
    log.info("Panel dimulai dengan config %s", config_path)

    # Satu panel = satu proses. Klik-ganda dua kali tanpa kunci ini membuat
    # dua proses berebut pin GPIO dan menggandakan hitungan counter.
    lock = single_instance(data_dir / "panel.lock")
    if lock is None:
        return fatal("Panel IoT CAKRA sudah berjalan di perangkat ini.")

    def report(kind: type[BaseException], value: BaseException, tb: object) -> None:
        log.error("Galat tak tertangani:\n%s", "".join(traceback.format_exception(kind, value, tb)))  # type: ignore[arg-type]

    sys.excepthook = report

    from .ui import PanelWindow

    window = PanelWindow(
        config,
        ApiClient(config.api_base_url, config.device_token),
        LocalStore(config.database_path, config.recovery_csv_path),
    )
    if args.windowed or not config.fullscreen:
        window.show()
    else:
        window.showFullScreen()
    code = application.exec_()
    lock.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
