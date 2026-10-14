"""Điểm vào Norilan.

    python main.py                  mở app (nếu đang chạy rồi thì đưa cửa sổ cũ lên trước)
    python main.py --seed           thêm dữ liệu mẫu rồi mở app
    python main.py --background     chạy ẩn ở khay (dùng khi khởi động cùng Windows)
    python main.py --open URL       mở đúng mục từ link norilan://… (dùng khi bấm vào toast)
"""
from __future__ import annotations

import argparse
import logging
import sys

from app import APP_NAME, __version__, paths
from app.db import connect
from app.demo_data import seed_demo
from app.logging_setup import setup_logging
from app.repository import Repository
from app.settings import load_settings
from app.system import APP_USER_MODEL_ID, WindowsIntegration
from app.timeutil import now

log = logging.getLogger(__name__)


def _windows_setup_process() -> None:
    """Gắn AppUserModelID (taskbar + toast cùng một app) và cho phép bản đang chạy tự đưa cửa sổ lên trước."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        log.warning("Không thiết lập được thông số Windows cho tiến trình", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--seed", action="store_true", help="thêm dữ liệu mẫu trước khi mở app")
    parser.add_argument("--background", action="store_true", help="chạy ẩn ở khay hệ thống")
    parser.add_argument("--open", metavar="URL", help="mở mục từ link norilan://…")
    args = parser.parse_args(argv)

    setup_logging(paths.log_dir())
    log.info("%s %s starting %s", APP_NAME, __version__, sys.argv[1:])
    _windows_setup_process()

    from PySide6.QtCore import QLocale, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from app.instance import InstanceServer, send_to_running

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)  # đóng cửa sổ ≠ thoát (app chạy ở khay)

    # Chỉ một bản chạy: nếu đã có, gửi lệnh sang rồi thoát.
    if args.open:
        message = {"cmd": "open", "url": args.open}
    else:
        message = {"cmd": "ping" if args.background else "show"}
    if send_to_running(message):
        log.info("Norilan đang chạy – đã chuyển lệnh %s", message)
        return 0
    server = InstanceServer()
    server.start()

    QLocale.setDefault(QLocale(QLocale.Language.Vietnamese, QLocale.Country.Vietnam))
    settings = load_settings(paths.settings_path())
    conn = connect(paths.db_path())
    repo = Repository(conn)
    if args.seed:
        log.info("Seeded %d demo items", seed_demo(repo, now()))

    from app.ui.app_controller import AppController

    integration = WindowsIntegration() if sys.platform == "win32" else None
    icon_path = paths.data_dir() / "icon.png"
    controller = AppController(app, repo, settings, settings_path=paths.settings_path(),
                               icon_path=icon_path, integration=integration)
    if integration is not None:
        try:
            integration.setup(icon_path=str(icon_path), autostart=settings.autostart)
        except OSError:
            log.exception("Không đăng ký được với Windows (toast/khởi động cùng Windows)")
    server.message_received.connect(controller.handle_message)

    if args.open:
        controller.open_url(args.open)
    elif not (args.background and controller.tray is not None):
        controller.show_window()

    code = app.exec()
    server.close()
    conn.close()
    log.info("%s exited with code %d", APP_NAME, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
