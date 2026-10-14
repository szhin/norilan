"""Cấu hình logging ra file xoay vòng + ghi lại mọi exception không bắt được."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """Gắn handler file (1 MB × 3 bản) và console (nếu có). Gọi nhiều lần vẫn an toàn."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "norilan.log"
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_norilan", False):
            root.removeHandler(handler)
            handler.close()

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler._norilan = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    # Bản .exe build dạng windowed không có stderr.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT))
        console.setLevel(logging.WARNING)
        console._norilan = True  # type: ignore[attr-defined]
        root.addHandler(console)

    def _excepthook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger("uncaught").critical("Lỗi không mong đợi", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook
    return log_file
