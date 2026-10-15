"""Cửa sổ chính: sidebar | (toolbar + lịch) | panel bên phải."""
from __future__ import annotations

import functools
import logging
from datetime import date, datetime, time, timedelta
from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, paths
from app.calendar_logic import (
    ViewMode,
    day_heading,
    items_by_day,
    period_title,
    relative_label,
    shift,
    visible_days,
)
from app.colors import ColorResolver, assign_missing_tones
from app.i18n import tr
from app.files import (
    AttachmentMissingError,
    FileService,
    attachment_from_target,
    is_app_copy,
    prepare_attachments,
    remove_orphan_copies,
)
from app.models import Attachment, Event, Task, TaskStatus
from app.repository import Repository
from app.settings import Settings
from app.timeutil import day_range, now as local_now
from app.ui import prompts
from app.ui.background import BackgroundWidget
from app.ui.calendar_views import DayView, MonthView, WeekView
from app.ui.dialogs import EventDialog, TaskDialog
from app.ui.side_panel import SidePanel
from app.ui.sidebar import Sidebar

log = logging.getLogger(__name__)

TICK_MS = 30_000  # cập nhật 'hôm nay' và số quá hạn
SEARCH_DELAY_MS = 220  # chờ ngừng gõ rồi mới tìm
LIST_NAVS = ("upcoming", "overdue", "search")  # panel phải là danh sách, không phải một ngày


def guarded(method: Callable) -> Callable:
    """Bọc slot: lỗi được ghi log và báo bằng hộp thoại thay vì làm app dừng."""

    @functools.wraps(method)
    def wrapper(self: "MainWindow", *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return method(self, *args, **kwargs)
        except Exception:
            log.exception("Lỗi trong %s", method.__name__)
            prompts.show_error(self, tr("error.generic"))
            return None

    return wrapper


class MainWindow(QMainWindow):
    settings_requested = Signal()
    hidden_to_tray = Signal()

    def __init__(
        self, repo: Repository, settings: Settings, *, files: FileService | None = None,
        clock: Callable[[], datetime] = local_now, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo = repo
        self._settings = settings
        self._files = files or FileService()
        self._clock = clock
        self._today: date = clock().date()
        self._selected: date = self._today
        self._view = ViewMode(settings.default_view) if settings.default_view in ViewMode._value2member_map_ else ViewMode.MONTH
        self._nav = "today"
        self._colors = ColorResolver(settings.color_by, repo.subject_colors())
        self.hide_on_close = False   # bật bởi AppController khi có khay hệ thống
        self._quitting = False
        self._search_text = ""
        self._search_status: TaskStatus | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.run_search)

        self.setWindowTitle(APP_NAME)
        self.resize(1320, 820)
        self.setMinimumSize(1040, 700)
        self._build_ui()
        self._build_shortcuts()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(TICK_MS)
        self.refresh()

    # ------------------------------------------------------------------ Dựng UI
    def _build_ui(self) -> None:
        central = self.central = BackgroundWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.nav_changed.connect(self.on_nav)
        self.sidebar.new_task_requested.connect(lambda: self.new_task())
        self.sidebar.settings_requested.connect(self.settings_requested)
        self.sidebar.search_changed.connect(self._on_search_typed)
        root.addWidget(self.sidebar)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self._build_toolbar())

        self.month_view = MonthView()
        self.week_view = WeekView()
        self.day_view = DayView()
        self._views = {ViewMode.MONTH: self.month_view, ViewMode.WEEK: self.week_view, ViewMode.DAY: self.day_view}
        self._stack = QStackedWidget()
        for view in self._views.values():
            self._stack.addWidget(view)
            view.item_clicked.connect(self.on_calendar_item)
            view.item_context.connect(self.show_item_menu)
        for grid in (self.month_view, self.week_view):
            grid.day_clicked.connect(self.on_day_clicked)
            grid.day_double_clicked.connect(self.new_task)
        cv.addWidget(self._stack, 1)

        self.panel = SidePanel()
        self.panel.item_clicked.connect(self.activate_item)
        self.panel.item_context.connect(self.show_item_menu)
        self.panel.status_filter_changed.connect(self._on_status_filter)
        detail = self.panel.detail
        detail.edit_requested.connect(self.edit_item)
        detail.delete_requested.connect(self.delete_item)
        detail.status_requested.connect(self.set_status)
        detail.open_attachment_requested.connect(self.open_attachment_by_id)
        detail.reveal_attachment_requested.connect(self.reveal_attachment_by_id)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(center)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([960, 330])
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame(objectName="Toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        today_btn = QPushButton(tr("toolbar.today"), objectName="ToolButton")
        today_btn.setToolTip(tr("toolbar.today_tooltip"))
        today_btn.clicked.connect(self.go_today)
        prev_btn = QPushButton("‹", objectName="ToolButton")
        prev_btn.setToolTip(tr("toolbar.prev_tooltip"))
        prev_btn.clicked.connect(lambda: self.go(-1))
        next_btn = QPushButton("›", objectName="ToolButton")
        next_btn.setToolTip(tr("toolbar.next_tooltip"))
        next_btn.clicked.connect(lambda: self.go(1))
        for btn in (today_btn, prev_btn, next_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.setFixedWidth(34)
        next_btn.setFixedWidth(34)
        self.period_label = QLabel(objectName="PeriodTitle")

        layout.addWidget(today_btn)
        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)
        layout.addWidget(self.period_label)
        layout.addStretch(1)

        new_task = QPushButton(tr("toolbar.new_task"), objectName="PrimaryButton")
        new_task.setToolTip(tr("toolbar.new_task_tooltip"))
        new_task.clicked.connect(lambda: self.new_task())
        new_event = QPushButton(tr("toolbar.new_event"))
        new_event.setToolTip(tr("toolbar.new_event_tooltip"))
        new_event.clicked.connect(lambda: self.new_event())
        for btn in (new_task, new_event):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(btn)
        layout.addSpacing(12)

        self._view_buttons: dict[ViewMode, QPushButton] = {}
        group = QButtonGroup(bar)  # thuộc toolbar: bị huỷ cùng toolbar khi dựng lại giao diện
        modes = list(ViewMode)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for index, mode in enumerate(modes):
            btn = QPushButton(mode.label, objectName="SegButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tr("toolbar.view_tooltip", view=mode.label.lower(), n=index + 1))
            btn.setProperty("pos", "first" if index == 0 else "last" if index == len(modes) - 1 else "mid")
            btn.clicked.connect(lambda _=False, m=mode: self.set_view(m))
            group.addButton(btn)
            seg.addWidget(btn)
            self._view_buttons[mode] = btn
        layout.addLayout(seg)
        return bar

    def _build_shortcuts(self) -> None:
        bindings: list[tuple[str, Callable[[], None]]] = [
            ("Ctrl+Left", lambda: self.go(-1)),
            ("Ctrl+Right", lambda: self.go(1)),
            ("PgUp", lambda: self.go(-1)),
            ("PgDown", lambda: self.go(1)),
            ("Ctrl+T", self.go_today),
            ("Ctrl+1", lambda: self.set_view(ViewMode.MONTH)),
            ("Ctrl+2", lambda: self.set_view(ViewMode.WEEK)),
            ("Ctrl+3", lambda: self.set_view(ViewMode.DAY)),
            # panel/sidebar được dựng lại khi đổi ngôn ngữ → luôn gọi qua self, không giữ hàm của widget cũ
            ("Esc", lambda: self.panel.show_list_page()),
            ("F5", self.refresh),
            ("Ctrl+N", lambda: self.new_task()),
            ("Ctrl+Shift+N", lambda: self.new_event()),
            ("Ctrl+E", lambda: self._on_detail(self.edit_item)),
            ("Delete", lambda: self._on_detail(self.delete_item)),
            ("Ctrl+,", self.settings_requested.emit),
            ("Ctrl+F", lambda: self.sidebar.focus_search()),
        ]
        for keys, handler in bindings:
            QShortcut(QKeySequence(keys), self).activated.connect(handler)

    # ------------------------------------------------------------------ Điều hướng
    @property
    def selected_day(self) -> date:
        return self._selected

    @property
    def view_mode(self) -> ViewMode:
        return self._view

    @guarded
    def go(self, step: int) -> None:
        self._selected = shift(self._view, self._selected, step)
        self._nav = self._nav_for_day(self._selected)
        self.refresh()

    @guarded
    def go_today(self) -> None:
        self._selected = self._today
        self._nav = "today"
        self.refresh()

    @guarded
    def set_view(self, mode: ViewMode) -> None:
        self._view = ViewMode(mode)
        self.refresh()

    @guarded
    def on_day_clicked(self, day: date) -> None:
        self._selected = day
        self._nav = self._nav_for_day(day)
        self.refresh()

    @guarded
    def on_calendar_item(self, item_id: int, day: date) -> None:
        self.on_day_clicked(day)
        self.activate_item(item_id)

    @guarded
    def activate_item(self, item_id: int) -> None:
        """Bấm vào mục (chip hoặc thẻ): hiện chi tiết bên phải."""
        self.open_item(item_id)

    @guarded
    def on_nav(self, key: str) -> None:
        self._leave_search()
        self._nav = key
        if key == "today":
            self._selected = self._today
        elif key == "tomorrow":
            self._selected = self._today + timedelta(days=1)
        self.refresh()

    @guarded
    def open_item(self, item_id: int) -> None:
        """Hiện chi tiết mục; nếu mục ở ngày khác thì lịch nhảy tới ngày đó (giữ nguyên danh sách bên phải)."""
        item = self._repo.get_item(item_id)
        if item is None:
            self.refresh()
            return
        day = item.starts_at.date()
        if day != self._selected:
            self._selected = day
            if self._nav in LIST_NAVS:
                self._refresh_calendar()  # giữ nguyên danh sách bên phải
            else:
                self._nav = self._nav_for_day(day)
                self.refresh()
        self.panel.show_detail(item, self._clock(), self._colors)
        self.day_view.set_selected_item(item_id)

    def bring_to_front(self) -> None:
        """Hiện cửa sổ (kể cả khi đang ẩn ở khay hoặc thu nhỏ) và đưa lên trước."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def focus_item(self, item_id: int) -> None:
        """Đưa cửa sổ lên và mở đúng mục (bấm vào toast nhắc việc)."""
        self.bring_to_front()
        if self._repo.get_item(item_id) is None:
            prompts.show_error(self, tr("item.gone"))
            return
        self.open_item(item_id)

    def focus_day(self, day: date) -> None:
        """Đưa cửa sổ lên và chọn một ngày (bấm vào toast tổng hợp 'Ngày mai…')."""
        self.bring_to_front()
        self.on_day_clicked(day)

    def set_background(self, pixmap, color: str) -> None:  # type: ignore[no-untyped-def]
        self.central.set_background(pixmap, color)

    # ------------------------------------------------------------------ Tìm kiếm
    def search(self, text: str) -> None:
        """Tìm ngay (không chờ) – dùng khi gọi từ code/test."""
        self.sidebar.search.blockSignals(True)
        self.sidebar.search.setText(text)
        self.sidebar.search.blockSignals(False)
        self._search_text = text
        self.run_search()

    def _on_search_typed(self, text: str) -> None:
        self._search_text = text
        self._search_timer.start()

    def _on_status_filter(self, status: TaskStatus | None) -> None:
        self._search_status = status
        self.run_search()

    @guarded
    def run_search(self) -> None:
        if self._search_text.strip() or self._search_status is not None:
            self._nav = "search"
        elif self._nav == "search":
            self._nav = self._nav_for_day(self._selected)
        self._refresh_sidebar()
        self._refresh_panel()

    def _leave_search(self) -> None:
        self._search_timer.stop()
        self._search_text = ""
        self._search_status = None
        self.panel.reset_filter()
        self.sidebar.search.blockSignals(True)
        self.sidebar.search.clear()
        self.sidebar.search.blockSignals(False)

    def apply_settings(self, settings: Settings) -> None:
        """Áp cài đặt mới (màu, chế độ tô màu…) mà không cần mở lại app."""
        self._settings = settings
        self._refresh_keep_detail()

    def retranslate(self) -> None:
        """Dựng lại giao diện theo ngôn ngữ vừa đổi, giữ nguyên ngày/chế độ xem, tìm kiếm và mục đang mở.

        Người gọi (AppController) áp lại ảnh nền và logo sau đó – chúng gắn với widget cũ đã bị thay.
        """
        detail_id = self.panel.detail_item_id()
        self._build_ui()
        self.sidebar.search.blockSignals(True)
        self.sidebar.search.setText(self._search_text)
        self.sidebar.search.blockSignals(False)
        if self._search_status is not None:
            self.panel.status_filter.blockSignals(True)
            self.panel.status_filter.setCurrentIndex(self.panel.status_filter.findData(self._search_status))
            self.panel.status_filter.blockSignals(False)
        self.refresh()
        if detail_id is not None and self._repo.get_item(detail_id) is not None:
            self.open_item(detail_id)

    # ------------------------------------------------------------------ Đóng cửa sổ
    def quit_app(self) -> None:
        """Đóng thật (menu Thoát) – không ẩn xuống khay nữa."""
        self._quitting = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hide_on_close and not self._quitting:
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
            return
        super().closeEvent(event)

    def _nav_for_day(self, day: date) -> str:
        if day == self._today:
            return "today"
        if day == self._today + timedelta(days=1):
            return "tomorrow"
        return "calendar"

    # ------------------------------------------------------------------ Thêm / sửa / xoá
    @guarded
    def new_task(self, day: date | None = None) -> None:
        due = datetime.combine(day or self._selected, time(23, 59))
        dialog = TaskDialog(files=self._files, subjects=self._repo.subjects(), default_due=due,
                            copy_default=self._settings.copy_attachments_to_app_dir,
                            subject_colors=self._repo.subject_colors(), parent=self)
        if self._exec_dialog(dialog):
            task = dialog.result_item()
            self._copy_files(task, dialog.copy_files.isChecked())
            self._save_subject_color(dialog)
            self._after_save(self._repo.add_task(task))

    @guarded
    def new_event(self, day: date | None = None) -> None:
        day = day or self._selected
        now = self._clock()
        if day == now.date():
            start = now.replace(minute=0, second=0) + timedelta(hours=1)
        else:
            start = datetime.combine(day, time(9, 0))
        dialog = EventDialog(default_start=start, parent=self)
        if self._exec_dialog(dialog):
            self._after_save(self._repo.add_event(dialog.result_item()))

    @guarded
    def edit_item(self, item_id: int) -> None:
        item = self._repo.get_item(item_id)
        if item is None:
            self.refresh()
            return
        if isinstance(item, Task):
            dialog = TaskDialog(files=self._files, subjects=self._repo.subjects(), task=item,
                                copy_default=self._settings.copy_attachments_to_app_dir,
                                subject_colors=self._repo.subject_colors(), parent=self)
            if self._exec_dialog(dialog):
                task = dialog.result_item()
                self._copy_files(task, dialog.copy_files.isChecked())
                self._save_subject_color(dialog)
                self._after_save(self._repo.update_task(task))
                self._cleanup_copies()  # bản sao của đính kèm vừa gỡ
        else:
            dialog = EventDialog(event=item, parent=self)
            if self._exec_dialog(dialog):
                self._after_save(self._repo.update_event(dialog.result_item()))

    @guarded
    def delete_item(self, item_id: int) -> None:
        item = self._repo.get_item(item_id)
        if item is None:
            return
        what = "task" if isinstance(item, Task) else "event"
        extra = ""
        if isinstance(item, Task) and item.attachments:
            extra = "\n\n" + tr("delete.extra_originals")
            if any(is_app_copy(a, paths.attachments_dir()) for a in item.attachments):
                extra += " " + tr("delete.extra_copies")
        if prompts.confirm(self, tr(f"delete.{what}_title"), tr(f"delete.{what}_text", title=item.title, extra=extra)):
            self._repo.delete_item(item_id)
            log.info("Deleted item #%d", item_id)
            self._cleanup_copies()
            self.refresh()

    @guarded
    def set_status(self, item_id: int, status: TaskStatus) -> None:
        task = self._repo.get_task(item_id)
        if task is None or task.status == status:
            return
        self._repo.set_task_status(item_id, status)
        self._refresh_keep_detail()

    def _after_save(self, item: Task | Event) -> None:
        """Sau khi lưu: nhảy tới ngày của mục và mở chi tiết của nó."""
        self._selected = item.starts_at.date()
        if self._nav not in LIST_NAVS:
            self._nav = self._nav_for_day(self._selected)
        self.refresh()
        self.open_item(item.id)  # type: ignore[arg-type]

    def _save_subject_color(self, dialog: TaskDialog) -> None:
        if (choice := dialog.subject_color_choice()) is not None:
            self._repo.set_subject_color(*choice)

    def _copy_files(self, task: Task, copy_files: bool) -> None:
        errors = prepare_attachments(task.attachments, copy_files=copy_files, dest_dir=paths.attachments_dir())
        if errors:
            prompts.show_error(self, tr("files.copy_failed"), "\n".join(errors))

    def _cleanup_copies(self) -> None:
        """Dọn bản sao không còn bài tập nào dùng. Lỗi ở đây không được làm hỏng thao tác chính."""
        try:
            remove_orphan_copies(paths.attachments_dir(), self._repo.all_attachment_targets())
        except Exception:
            log.exception("Dọn bản sao thất bại")

    def _exec_dialog(self, dialog: QDialog) -> bool:
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _on_detail(self, action: Callable[[int], None]) -> None:
        if (item_id := self.panel.detail_item_id()) is not None:
            action(item_id)

    # ------------------------------------------------------------------ Đính kèm
    @guarded
    def open_attachment_by_id(self, attachment_id: int) -> None:
        att = self._repo.get_attachment(attachment_id)
        if att is None:
            self._refresh_keep_detail()
        else:
            self.open_attachment(att)

    @guarded
    def reveal_attachment_by_id(self, attachment_id: int) -> None:
        att = self._repo.get_attachment(attachment_id)
        if att is None:
            return
        try:
            self._files.reveal(att)
        except AttachmentMissingError:
            prompts.show_error(self, tr("attach.missing_both", name=att.name), att.target)
        except OSError as exc:
            log.warning("Reveal failed: %s", exc)
            prompts.show_error(self, tr("attach.reveal_failed"), str(exc))

    def open_attachment(self, att: Attachment) -> None:
        try:
            self._files.open(att)
        except AttachmentMissingError:
            self._handle_missing(att)
        except OSError as exc:
            log.warning("Open failed for %s: %s", att.target, exc)
            prompts.show_error(
                self, tr("attach.open_failed", name=att.name), tr("attach.open_failed_hint", error=exc),
            )

    def _handle_missing(self, att: Attachment) -> None:
        """File đã bị di chuyển/xoá: cho chọn lại đường dẫn hoặc gỡ đính kèm – không bao giờ crash."""
        action = prompts.ask_missing_action(self, att)
        if action == "relink":
            path = prompts.choose_replacement(self, att)
            if not path:
                return
            replacement = attachment_from_target(path)
            att.target, att.kind = replacement.target, replacement.kind
            self._repo.update_attachment(att)
            log.info("Relinked attachment #%s → %s", att.id, att.target)
            self._refresh_keep_detail()
            self.open_attachment(att)
        elif action == "remove":
            self._repo.remove_attachment(att.id)  # type: ignore[arg-type]
            self._cleanup_copies()
            self._refresh_keep_detail()

    # ------------------------------------------------------------------ Menu chuột phải
    def build_item_menu(self, item_id: int) -> QMenu | None:
        item = self._repo.get_item(item_id)
        if item is None:
            return None
        menu = QMenu(self)
        menu.addAction(tr("menu.details"), lambda: self.open_item(item_id))
        if isinstance(item, Task) and len(item.file_attachments) == 1:
            att = item.file_attachments[0]
            menu.addAction(tr("menu.open_file", name=att.name), lambda: self.open_attachment(att))
        menu.addAction(tr("menu.edit"), lambda: self.edit_item(item_id))
        if isinstance(item, Task):
            if item.is_done:
                menu.addAction(tr("menu.mark_todo"), lambda: self.set_status(item_id, TaskStatus.TODO))
            else:
                menu.addAction(tr("menu.mark_done"), lambda: self.set_status(item_id, TaskStatus.DONE))
        menu.addSeparator()
        menu.addAction(tr("menu.delete"), lambda: self.delete_item(item_id))
        return menu

    @guarded
    def show_item_menu(self, item_id: int, pos: QPoint) -> None:
        if (menu := self.build_item_menu(item_id)) is not None:
            menu.exec(pos)

    # ------------------------------------------------------------------ Làm mới
    @guarded
    def refresh(self) -> None:
        subject_colors = self._repo.subject_colors()
        new_tones = assign_missing_tones(list(reversed(self._repo.subjects())), subject_colors)
        for subject, tone in new_tones.items():  # môn cũ chưa có màu → lưu màu cố định
            self._repo.set_subject_color(subject, tone)
        subject_colors.update(new_tones)
        self._colors = ColorResolver(self._settings.color_by, subject_colors)
        self._refresh_calendar()
        self._refresh_sidebar()
        self._refresh_panel()

    def _refresh_keep_detail(self) -> None:
        """Làm mới mọi thứ nhưng giữ trang chi tiết đang mở (nếu mục còn tồn tại)."""
        detail_id = self.panel.detail_item_id()
        self.refresh()
        if detail_id is not None and self._repo.get_item(detail_id) is not None:
            self.open_item(detail_id)

    def _refresh_calendar(self) -> None:
        now = self._clock()
        days = visible_days(self._view, self._selected)
        start, _ = day_range(days[0])
        _, end = day_range(days[-1])
        grouped = items_by_day(self._repo.items_between(start, end), days)
        self._views[self._view].set_data(self._selected, grouped, today=self._today, now=now, colors=self._colors)
        self._stack.setCurrentWidget(self._views[self._view])
        self._view_buttons[self._view].setChecked(True)
        self.period_label.setText(period_title(self._view, self._selected))

    def _refresh_sidebar(self) -> None:
        now = self._clock()
        tomorrow = self._today + timedelta(days=1)
        self.sidebar.set_counts({
            "today": len(self._repo.items_on(self._today, include_done=False)),
            "tomorrow": len(self._repo.items_on(tomorrow, include_done=False)),
            "upcoming": len(self._repo.upcoming(now)),
            "overdue": len(self._repo.overdue_tasks(now)),
        })
        self.sidebar.set_current(self._nav)

    def _refresh_panel(self) -> None:
        now = self._clock()
        self.panel.set_filter_visible(self._nav == "search")
        if self._nav == "search":
            text = self._search_text.strip()
            items = self._repo.search(text, status=self._search_status)
            count = len(items)
            if text:
                subtitle = tr("search.results", count=count, text=text)
            else:
                status = self._search_status.label.lower()  # type: ignore[union-attr]
                subtitle = tr("search.status_tasks", count=count, status=status)
            self.panel.show_items(tr("search.title"), subtitle, items, now=now, today=self._today,
                                  group_by_day=True, colors=self._colors, empty_text=tr("search.empty"))
        elif self._nav == "upcoming":
            items = self._repo.upcoming(now)
            self.panel.show_items(tr("upcoming.title"), tr("upcoming.subtitle", count=len(items)), items,
                                  now=now, today=self._today, group_by_day=True, colors=self._colors,
                                  empty_text=tr("upcoming.empty"))
        elif self._nav == "overdue":
            items = self._repo.overdue_tasks(now)
            self.panel.show_items(tr("overdue.title"), tr("overdue.subtitle", count=len(items)), items,
                                  now=now, today=self._today, group_by_day=True, colors=self._colors,
                                  empty_text=tr("overdue.empty"))
        else:
            items = self._repo.items_on(self._selected)
            rel = relative_label(self._selected, self._today)
            if rel:
                subtitle = tr("day.subtitle_rel", rel=rel, count=len(items))
            else:
                subtitle = tr("day.subtitle", count=len(items))
            self.panel.show_items(day_heading(self._selected), subtitle, items,
                                  now=now, today=self._today, colors=self._colors, empty_text=tr("day.empty"))

    def _on_tick(self) -> None:
        """Qua nửa đêm thì cập nhật 'hôm nay'; luôn cập nhật số đếm (việc có thể vừa quá hạn)."""
        try:
            today = self._clock().date()
            if today != self._today:
                was_on_today = self._selected == self._today
                self._today = today
                if was_on_today:
                    self._selected = today
                self.refresh()
            else:
                self._refresh_sidebar()
        except Exception:
            log.exception("Lỗi khi cập nhật định kỳ")
