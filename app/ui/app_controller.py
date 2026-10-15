"""Điều phối toàn app: cửa sổ chính, khay hệ thống, nhắc việc (QTimer), thông báo, cài đặt."""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import sqlite3

from PySide6.QtCore import QObject, QStandardPaths, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from app import APP_NAME, links, paths
from app.backup import BackupError, create_backup, default_backup_name, prune_backups, read_manifest, restore_backup
from app.colors import accent_color
from app.files import FileService
from app.i18n import tr
from app.maintenance import count_purgeable, purge_done
from app.notifier import Notifier, RecordingNotifier, WinotifyNotifier, winotify_available
from app.reminders import Notification, ReminderService
from app.repository import Repository
from app.settings import Settings, load_settings, save_settings
from app.system import APP_USER_MODEL_ID, WindowsIntegration
from app.timeutil import now as local_now
from app.tray import Tray
from app.ui import prompts
from app.ui.background import cleanup_backgrounds, load_background
from app.ui.icon import app_icon, render_icon, save_icon_png
from app.ui.language import apply_language
from app.ui.main_window import MainWindow
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme, current_tokens, resolve_dark

log = logging.getLogger(__name__)

REMINDER_INTERVAL_MS = 20_000   # kiểm tra nhắc việc mỗi 20 giây
FIRST_CHECK_DELAY_MS = 3_000    # lần đầu: vài giây sau khi mở app
SAFETY_BACKUPS_KEPT = 5


def backup_filter() -> str:
    return f"{tr('backup.filter')} (*.zip)"


class TrayNotifier:
    """Dự phòng khi không có winotify: bong bóng thông báo của khay hệ thống."""

    def __init__(self, tray: Tray) -> None:
        self._tray = tray
        self.last_url: str | None = None

    def show(self, note: Notification) -> None:
        self.last_url = note.url
        self._tray.notify(note.title, note.body)


class AppController(QObject):
    def __init__(
        self, app: QApplication, repo: Repository, settings: Settings, *,
        settings_path: Path, icon_path: Path | None = None, files: FileService | None = None,
        notifier: Notifier | None = None, integration: WindowsIntegration | None = None,
        clock: Callable[[], datetime] = local_now, use_tray: bool | None = None,
    ) -> None:
        super().__init__()
        self._app = app
        self._repo = repo
        self._settings = settings
        self._settings_path = settings_path
        self._icon_path = icon_path
        self._integration = integration
        self._clock = clock
        self._files = files or FileService()
        self._data_dir = settings_path.parent
        self._bg_cache: dict[tuple[str, bool], QPixmap | None] = {}

        apply_language(settings.language)  # trước khi dựng bất kỳ widget nào
        self.window = MainWindow(repo, settings, files=self._files, clock=clock)
        self.window.settings_requested.connect(self.open_settings)
        self.window.hidden_to_tray.connect(self._on_hidden_to_tray)

        self.tray: Tray | None = None
        if Tray.available() if use_tray is None else use_tray:
            self.tray = Tray(app_icon(), self)
            self.tray.open_requested.connect(self.show_window)
            self.tray.quick_add_requested.connect(self.quick_add)
            self.tray.settings_requested.connect(self.open_settings)
            self.tray.quit_requested.connect(self.quit)
            self.tray.message_clicked.connect(self._on_tray_message_clicked)
            self.tray.show()
        self.window.hide_on_close = self.tray is not None and settings.minimize_to_tray

        if notifier is not None:
            self.notifier = notifier
        elif winotify_available():
            self.notifier = WinotifyNotifier(APP_USER_MODEL_ID, str(icon_path or ""))
        elif self.tray is not None:
            self.notifier = TrayNotifier(self.tray)
        else:
            self.notifier = RecordingNotifier()

        self.reminders = ReminderService(repo, lambda: self._settings)
        self._timer = QTimer(self)
        self._timer.setInterval(REMINDER_INTERVAL_MS)
        self._timer.timeout.connect(self.check_reminders)
        self._timer.start()
        # Lần kiểm tra đầu tiên ngay sau khi mở app (QTimer riêng để còn huỷ được khi thoát).
        self._first_check = QTimer(self)
        self._first_check.setSingleShot(True)
        self._first_check.setInterval(FIRST_CHECK_DELAY_MS)
        self._first_check.timeout.connect(self.check_reminders)
        self._first_check.start()

        try:  # Windows đổi sáng/tối khi đang chạy → đổi theo nếu chọn 'Theo Windows'
            app.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except AttributeError:
            pass
        self._apply_look()
        self._update_tray_tooltip()

    @property
    def settings(self) -> Settings:
        return self._settings

    # ------------------------------------------------------------------ Cửa sổ
    def show_window(self) -> None:
        self.window.bring_to_front()

    def quick_add(self) -> None:
        self.window.new_task()

    def handle_message(self, message: dict) -> None:
        """Lệnh từ lần mở thứ hai (vd. bấm vào toast, hoặc mở lại Norilan khi đang chạy)."""
        log.info("Message from another instance: %s", message)
        cmd = message.get("cmd")
        if cmd == "open" and isinstance(message.get("url"), str):
            self.open_url(message["url"])
        elif cmd == "show":
            self.show_window()

    def open_url(self, url: str) -> None:
        parsed = links.parse(url)
        if parsed is None:
            self.show_window()
        elif parsed[0] == "item":
            self.window.focus_item(parsed[1])  # type: ignore[arg-type]
        else:
            self.window.focus_day(parsed[1])  # type: ignore[arg-type]

    def shutdown(self) -> None:
        """Dừng mọi bộ hẹn giờ – sau đó không còn truy cập database nữa."""
        self._timer.stop()
        self._first_check.stop()
        if self.tray is not None:
            self.tray.hide()

    def quit(self) -> None:
        log.info("Quit requested")
        self.shutdown()
        self.window.quit_app()
        self._app.quit()

    def _on_hidden_to_tray(self) -> None:
        if self.tray is not None and not self._settings.tray_hint_shown:
            self.tray.notify(tr("tray.hint_title", app=APP_NAME), tr("tray.hint_body"))
            self._save(replace(self._settings, tray_hint_shown=True))

    def _on_tray_message_clicked(self) -> None:
        if isinstance(self.notifier, TrayNotifier) and self.notifier.last_url:
            self.open_url(self.notifier.last_url)
        else:
            self.show_window()

    # ------------------------------------------------------------------ Nhắc việc
    def check_reminders(self) -> list[Notification]:
        try:
            notes = self.reminders.check(self._clock())
            for note in notes:
                self.notifier.show(note)
            self.run_maintenance()
            self._update_tray_tooltip()
            return notes
        except Exception:
            log.exception("Lỗi khi kiểm tra nhắc việc")
            return []

    def run_maintenance(self, *, force: bool = False) -> int:
        """Tự xoá bài tập đã xong quá N ngày (mỗi ngày một lần, hoặc ngay khi vừa đổi cài đặt)."""
        removed = purge_done(self._repo, self._settings.auto_delete_done_days, self._clock(),
                             attachments_dir=self._data_dir / "attachments", force=force)
        if removed:
            self.window._refresh_keep_detail()
        return removed

    def _update_tray_tooltip(self) -> None:
        if self.tray is None:
            return
        now = self._clock()
        today = len(self._repo.items_on(now.date(), include_done=False))
        tomorrow = len(self._repo.items_on(now.date() + timedelta(days=1), include_done=False))
        overdue = len(self._repo.overdue_tasks(now))
        text = tr("tray.tooltip", app=APP_NAME, today=today, tomorrow=tomorrow)
        if overdue:
            text += "\n" + tr("tray.tooltip_overdue", overdue=overdue)
        self.tray.set_tooltip(text)

    # ------------------------------------------------------------------ Cài đặt
    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self._settings, subjects=self._repo.subjects(), subject_colors=self._repo.subject_colors(),
            autostart_supported=self._integration is not None, parent=self.window,
            purge_preview=lambda days: count_purgeable(self._repo, days, self._clock()),
        )
        dialog.preview_changed.connect(self._apply_look)
        dialog.backup_requested.connect(lambda: self.backup(dialog))
        dialog.restore_requested.connect(lambda: self._restore_from_settings(dialog))
        dialog.open_data_requested.connect(self.open_data_folder)
        accepted = self._exec_dialog(dialog)
        if dialog.restored:
            pass  # đã khôi phục: cài đặt mới lấy từ bản sao lưu, bỏ qua các thay đổi trong cửa sổ này
        elif accepted:
            for subject, tone in dialog.subject_color_changes().items():
                self._repo.set_subject_color(subject, tone)
            self.apply_settings(dialog.result_settings())
        else:
            self._apply_look()  # huỷ → bỏ phần xem trước
        cleanup_backgrounds({self._settings.background} - {""})  # ảnh đã chọn thử nhưng không dùng

    def _exec_dialog(self, dialog: QDialog) -> bool:
        if not self.window.isVisible():
            self.show_window()
        return dialog.exec() == QDialog.DialogCode.Accepted

    def apply_settings(self, new: Settings) -> None:
        old = self._settings
        self._save(new)
        self.window.hide_on_close = self.tray is not None and new.minimize_to_tray
        if new.auto_delete_done_days != old.auto_delete_done_days and new.auto_delete_done_days > 0:
            self.run_maintenance(force=True)
        if self._integration is not None and new.autostart != old.autostart:
            try:
                self._integration.set_autostart(new.autostart)
            except OSError as exc:
                log.exception("Không đổi được khởi động cùng Windows")
                prompts.show_error(self.window, tr("autostart.error"), str(exc))
        if new.language != old.language:
            self._apply_language(new.language)
        self._apply_look()
        self.window.apply_settings(new)
        self._update_tray_tooltip()

    def _apply_language(self, code: str) -> None:
        """Đổi ngôn ngữ ngay, không cần mở lại app: dựng lại cửa sổ chính và menu khay.
        Ảnh nền, logo… gắn với widget cũ nên người gọi phải _apply_look() sau đó."""
        apply_language(code)
        self.window.retranslate()
        if self.tray is not None:
            self.tray.retranslate()

    def _save(self, settings: Settings) -> None:
        self._settings = settings
        try:
            save_settings(settings, self._settings_path)
        except (OSError, ValueError):
            log.exception("Không lưu được settings")

    def _background_pixmap(self, name: str, blur: bool) -> QPixmap | None:
        key = (name, blur)
        if key not in self._bg_cache:
            self._bg_cache.clear()  # chỉ giữ ảnh đang dùng
            self._bg_cache[key] = load_background(name, blur=blur) if name else None
        return self._bg_cache[key]

    def _apply_look(self, preview: Settings | None = None) -> None:
        """Áp theme, màu nhấn, ảnh nền, icon. `preview` = cài đặt đang chỉnh (chưa lưu)."""
        settings = preview or self._settings
        theme, accent = settings.theme, settings.accent
        pixmap = self._background_pixmap(settings.background, settings.background_blur)
        apply_theme(self._app, theme, accent, settings.background_visibility if pixmap is not None else None)
        self.window.set_background(pixmap, current_tokens()["bg"])
        color = accent_color(accent, dark=resolve_dark(theme))
        icon = app_icon(color)
        self._app.setWindowIcon(icon)
        self.window.setWindowIcon(icon)
        logo = render_icon(44, color)
        logo.setDevicePixelRatio(2)
        self.window.sidebar.set_logo(logo)
        if self.tray is not None:
            self.tray.set_icon(icon)
        if self._icon_path is not None:
            try:
                save_icon_png(self._icon_path, color)
            except OSError:
                log.warning("Không ghi được icon cho toast", exc_info=True)

    # ------------------------------------------------------------------ Sao lưu / khôi phục
    def backup(self, parent: QWidget | None = None) -> Path | None:
        now = self._clock()
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path = prompts.ask_save_path(parent or self.window, tr("backup.save_title"),
                                     str(Path(documents) / default_backup_name(now)), backup_filter())
        if not path:
            return None
        dest = Path(path) if path.lower().endswith(".zip") else Path(path + ".zip")
        try:
            create_backup(self._repo.connection, dest, data_dir=self._data_dir, now=now)
        except (OSError, sqlite3.Error) as exc:
            log.exception("Sao lưu thất bại")
            prompts.show_error(parent or self.window, tr("backup.failed"), str(exc))
            return None
        prompts.show_info(parent or self.window, tr("backup.done"), str(dest))
        return dest

    def restore(self, parent: QWidget | None = None) -> bool:
        parent = parent or self.window
        path = prompts.ask_open_path(parent, tr("restore.pick_title"), backup_filter())
        if not path:
            return False
        try:
            manifest = read_manifest(Path(path))
        except BackupError as exc:
            prompts.show_error(parent, str(exc))
            return False
        created = str(manifest.get("created_at", "?")).replace("T", tr("restore.created_sep"))
        if not prompts.confirm(
            parent, tr("restore.confirm_title"), tr("restore.confirm_text", created=created),
            ok_text=tr("restore.confirm_ok"),
        ):
            return False
        now = self._clock()
        try:
            safety = paths.backups_dir() / f"truoc-khi-khoi-phuc-{now:%Y%m%d-%H%M%S}.zip"
            create_backup(self._repo.connection, safety, data_dir=self._data_dir, now=now)
            prune_backups(paths.backups_dir(), keep=SAFETY_BACKUPS_KEPT)
            restore_backup(self._repo.connection, Path(path), data_dir=self._data_dir)
        except BackupError as exc:
            prompts.show_error(parent, str(exc))
            return False
        except (OSError, sqlite3.Error) as exc:
            log.exception("Khôi phục thất bại")
            prompts.show_error(parent, tr("restore.failed"), str(exc))
            return False
        # Khởi động cùng Windows là cài đặt của máy này, không lấy theo bản sao lưu.
        restored = replace(load_settings(self._settings_path), autostart=self._settings.autostart,
                           tray_hint_shown=self._settings.tray_hint_shown)
        self._bg_cache.clear()
        self.apply_settings(restored)
        prompts.show_info(parent, tr("restore.done"), tr("restore.done_detail", path=safety))
        return True

    def _restore_from_settings(self, dialog: SettingsDialog) -> None:
        if self.restore(dialog):
            dialog.restored = True
            dialog.reject()

    def open_data_folder(self) -> None:
        try:
            self._files.open_folder(str(self._data_dir))
        except OSError as exc:
            prompts.show_error(self.window, tr("folder.open_failed"), str(exc))

    def _on_system_scheme_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if self._settings.theme == "system":
            self._apply_look()
            self.window.refresh()
