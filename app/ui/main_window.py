"""Cửa sổ chính: sidebar | (toolbar + lịch) | panel bên phải."""
from __future__ import annotations

import functools
import logging
from datetime import date, datetime, time, timedelta
from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QKeySequence, QShowEvent, QShortcut
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

from app import APP_NAME, ai_import, paths
from app.calendar_logic import (
    ViewMode,
    day_heading,
    items_by_day,
    period_title,
    relative_label,
    shift,
    visible_days,
)
from app.colors import ColorResolver, assign_missing_tones, pick_tone
from app.i18n import current_language, tr
from app.files import (
    AttachmentMissingError,
    FileService,
    attachment_from_target,
    is_app_copy,
    prepare_attachments,
    remove_orphan_copies,
)
from app.maintenance import time_until_delete
from app.models import Attachment, ClassSession, Course, Deadline, DeadlineStatus, Event, StudyTime
from app.repository import Repository
from app.settings import Settings
from app.timeutil import day_range, now as local_now
from app.ui import prompts
from app.ui.ai_import_dialog import AiImportDialog
from app.ui.background import BackgroundWidget
from app.ui.calendar_views import DayView, MonthView, WeekView
from app.ui.dialogs import CourseDialog, DeadlineDialog, EventDialog, StudyTimeDialog
from app.ui.overlay import StudyOverlay
from app.ui.side_panel import SidePanel
from app.ui.sidebar import Sidebar

log = logging.getLogger(__name__)

TICK_MS = 30_000  # cập nhật 'hôm nay' và số quá hạn
SEARCH_DELAY_MS = 220  # chờ ngừng gõ rồi mới tìm
COUNTDOWN_TICK_MS = 1000  # đồng hồ đếm ngược Study time
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
        self._editing_ids: set[int] = set()   # Deadline đang mở trong hộp thoại sửa: tự xoá bỏ qua
        self._search_text = ""
        self._search_status: DeadlineStatus | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.run_search)

        # Một QTimer 1 giây dùng chung cho đồng hồ nhỏ và overlay; chỉ chạy khi một trong hai đang hiện.
        self._second_timer = QTimer(self)
        self._second_timer.setInterval(COUNTDOWN_TICK_MS)
        self._second_timer.timeout.connect(self._on_second)

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
        self.sidebar.new_deadline_requested.connect(lambda: self.new_deadline())
        self.sidebar.settings_requested.connect(self.settings_requested)
        self.sidebar.search_changed.connect(self._on_search_typed)
        self.sidebar.new_course_requested.connect(lambda: self.new_course())
        self.sidebar.course_clicked.connect(self.open_course)
        self.sidebar.course_context.connect(self.show_course_menu)
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
        for grid in (self.month_view, self.week_view, self.day_view):
            grid.day_clicked.connect(self.on_day_clicked)
            grid.day_double_clicked.connect(self.new_deadline)
        cv.addWidget(self._stack, 1)

        self.panel = SidePanel()
        self.panel.item_clicked.connect(self.activate_item)
        self.panel.item_context.connect(self.show_item_menu)
        self.panel.status_filter_changed.connect(self._on_status_filter)
        self.panel.page_changed.connect(self._sync_countdown_timer)
        self.panel.page_changed.connect(lambda: self.sidebar.set_current_course(self.panel.course_id()))
        course_view = self.panel.course_view
        course_view.edit_requested.connect(self.edit_course)
        course_view.delete_requested.connect(self.delete_course)
        course_view.add_deadline_requested.connect(lambda subject: self.new_deadline(subject=subject))
        course_view.add_study_requested.connect(lambda subject: self.new_study_time(subject=subject))
        detail = self.panel.detail
        detail.edit_requested.connect(self.edit_item)
        detail.delete_requested.connect(self.delete_item)
        detail.status_requested.connect(self.set_status)
        detail.open_attachment_requested.connect(self.open_attachment_by_id)
        detail.reveal_attachment_requested.connect(self.reveal_attachment_by_id)
        detail.add_study_requested.connect(self.add_study_for_deadline)
        detail.open_item_requested.connect(self.open_item)
        detail.countdown_clicked.connect(self.open_overlay)
        detail.open_course_requested.connect(self.open_course)

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
        # Overlay đếm ngược: con của central (phủ kín cửa sổ), dựng lại cùng giao diện khi đổi ngôn ngữ.
        self.overlay = StudyOverlay(central)
        self.overlay.closed.connect(self._sync_countdown_timer)

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

        new_deadline = QPushButton(tr("toolbar.new_deadline"), objectName="PrimaryButton")
        new_deadline.setToolTip(tr("toolbar.new_deadline_tooltip"))
        new_deadline.clicked.connect(lambda: self.new_deadline())
        new_study = QPushButton(tr("toolbar.new_study"))
        new_study.setToolTip(tr("toolbar.new_study_tooltip"))
        new_study.clicked.connect(lambda: self.new_study_time())
        new_event = QPushButton(tr("toolbar.new_event"))
        new_event.setToolTip(tr("toolbar.new_event_tooltip"))
        new_event.clicked.connect(lambda: self.new_event())
        for btn in (new_deadline, new_study, new_event):
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
            ("Esc", self._on_escape),
            ("F5", self.refresh),
            ("Ctrl+N", lambda: self.new_deadline()),
            ("Ctrl+Alt+N", lambda: self.new_study_time()),
            ("Ctrl+Shift+N", lambda: self.new_event()),
            ("Ctrl+E", lambda: self._on_detail(self.edit_item, self.edit_course)),
            ("Delete", lambda: self._on_detail(self.delete_item, self.delete_course)),
            ("Ctrl+,", self.settings_requested.emit),
            ("Ctrl+F", lambda: self.sidebar.focus_search()),
        ]
        for keys, handler in bindings:
            if keys != "Esc":
                handler = self._unless_overlay(handler)
            QShortcut(QKeySequence(keys), self).activated.connect(handler)

    def _unless_overlay(self, handler: Callable[[], None]) -> Callable[[], None]:
        """Overlay mở thì chặn phím tắt của giao diện bên dưới (chỉ Esc còn tác dụng, để đóng overlay)."""
        return lambda: None if self._overlay_open() else handler()

    def _on_escape(self) -> None:
        # Một handler duy nhất cho Esc (không đăng ký hai shortcut trùng phím – Qt sẽ coi là mơ hồ và bỏ cả hai).
        if self._overlay_open():
            self.overlay.close_overlay()
        else:
            self.panel.show_list_page()

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
        linked = self._repo.get_deadline(item.deadline_id) if isinstance(item, StudyTime) and item.deadline_id else None
        studies = self._repo.study_times_for(item.id) if isinstance(item, Deadline) else None
        self.panel.show_detail(item, self._clock(), self._colors, linked_deadline=linked, study_times=studies,
                               auto_delete_in=self._auto_delete_remaining(item))
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

    def _on_status_filter(self, status: DeadlineStatus | None) -> None:
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
        course_id = self.panel.course_id()
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
        elif course_id is not None and self._repo.get_course(course_id) is not None:
            self.open_course(course_id)

    # ------------------------------------------------------------------ Đếm ngược Study time
    def _overlay_open(self) -> bool:
        return self.overlay.is_open()

    @guarded
    def open_overlay(self, study_id: int) -> None:
        """Phóng to đồng hồ của một Study time (chỉ từ click của người dùng, không bao giờ tự mở)."""
        study = self._repo.get_study_time(study_id)
        if study is None:
            self.refresh()
            return
        deadline = self._repo.get_deadline(study.deadline_id) if study.deadline_id else None
        self.overlay.show_for(study, deadline, self._clock())
        self._sync_countdown_timer()

    def _refresh_overlay(self) -> None:
        """Dữ liệu vừa đổi (sửa/xoá): nạp lại overlay đang mở, hoặc đóng nếu Study time không còn."""
        if not self._overlay_open():
            return
        study = self._repo.get_study_time(self.overlay.study_id)  # type: ignore[arg-type]
        if study is None:
            self.overlay.close_overlay()
            return
        deadline = self._repo.get_deadline(study.deadline_id) if study.deadline_id else None
        self.overlay.show_for(study, deadline, self._clock())

    def _countdown_visible(self) -> bool:
        """Có thứ gì đang hiện cần đồng hồ chạy không? (đồng hồ nhỏ trong panel hoặc overlay)"""
        if not self.isVisible():
            return False
        # Không dựa vào isVisible() của widget con: widget vừa thêm chỉ "visible" sau lượt layout kế tiếp.
        # Đồng hồ nhỏ chỉ tồn tại ở trang chi tiết của Study time, nên có widget + đang ở trang chi tiết là đủ.
        small = self.panel.is_showing_detail() and self.panel.detail.countdown_widget is not None
        return small or self._overlay_open()

    def _sync_countdown_timer(self) -> None:
        if self._countdown_visible():
            if not self._second_timer.isActive():
                self._second_timer.start()
            self._update_countdowns()
        else:
            self._second_timer.stop()

    def _update_countdowns(self) -> None:
        """Tính lại từ giờ hệ thống (không bao giờ giảm một biến đếm)."""
        now = self._clock()
        self.panel.detail.update_countdown(now)
        if self._overlay_open():
            self.overlay.update_state(now)

    def _on_second(self) -> None:
        try:
            if self._countdown_visible():
                self._update_countdowns()
            else:
                self._second_timer.stop()
        except Exception:
            log.exception("Lỗi khi cập nhật đồng hồ đếm ngược")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_countdown_timer)  # con của cửa sổ chỉ "visible" sau showEvent

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._second_timer.stop()   # ẩn xuống khay: không tick nữa

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
    def new_deadline(self, day: date | None = None, *, subject: str = "") -> None:
        """Bấm thêm Deadline: chọn nhập thủ công hoặc đọc từ ảnh."""
        choice = prompts.choose_deadline_source(self)
        if choice == "manual":
            self.new_deadline_manual(day, subject=subject)
        elif choice == "image":
            self.new_deadlines_from_images()

    @guarded
    def new_deadlines_from_images(self) -> None:
        if not ai_import.load_api_key() and not prompts.edit_api_key(self):
            return
        today = self._clock().date()
        language = current_language()

        def extract(images):  # type: ignore[no-untyped-def]  # chạy ở luồng nền: chỉ gọi mạng, không đụng DB/Qt
            return ai_import.extract_deadlines(images, ai_import.load_api_key(), today=today, language=language)

        dialog = AiImportDialog(subjects=self._repo.subjects(), today=today, extract=extract,
                                is_duplicate=self._repo.deadline_exists, parent=self)
        if not self._exec_dialog(dialog):
            return
        entries = dialog.entries()
        if not entries:
            return
        colors = self._repo.subject_colors()
        for entry in entries:
            subject = entry.deadline.subject
            if subject and subject not in colors:
                colors[subject] = pick_tone(colors.values())
                self._repo.set_subject_color(subject, colors[subject])
        saved = []
        for entry in entries:
            deadline = self._repo.add_deadline(entry.deadline)
            saved.append(deadline)
            if entry.study is not None:   # Study time chỉ được tạo khi người dùng chủ động đặt giờ ở dòng đó
                start, end = entry.study
                self._repo.add_study_time(StudyTime(deadline.title, start, end, subject=deadline.subject,
                                                    deadline_id=deadline.id))
        log.info("Imported %d deadlines from image", len(saved))
        self._after_save(min(saved, key=lambda d: d.due_at))

    @guarded
    def new_deadline_manual(self, day: date | None = None, *, subject: str = "") -> None:
        due = datetime.combine(day or self._selected, time(23, 59))
        dialog = DeadlineDialog(files=self._files, subjects=self._repo.subjects(), default_due=due,
                            copy_default=self._settings.copy_attachments_to_app_dir,
                            subject_colors=self._repo.subject_colors(), subject=subject, parent=self)
        if self._exec_dialog(dialog):
            deadline = dialog.result_item()
            self._copy_files(deadline, dialog.copy_files.isChecked())
            self._save_subject_color(dialog)
            saved = self._repo.add_deadline(deadline)
            self._after_save(saved)
            if prompts.ask_study_after_deadline(self):
                self.add_study_for_deadline(saved.id)  # type: ignore[arg-type]

    @guarded
    def add_study_for_deadline(self, deadline_id: int) -> None:
        """Mở hộp thoại Study time điền sẵn tiêu đề (sao chép một lần) và liên kết với deadline này."""
        deadline = self._repo.get_deadline(deadline_id)
        if deadline is None:
            self.refresh()
            return
        self.new_study_time(title=deadline.title, deadline_id=deadline_id)

    @guarded
    def new_study_time(
        self, day: date | None = None, *, title: str = "", deadline_id: int | None = None, subject: str = "",
    ) -> None:
        day = day or self._clock().date()
        now = self._clock()
        if day == now.date():
            start = now.replace(minute=0, second=0) + timedelta(hours=1)
        else:
            start = datetime.combine(day, time(9, 0))
        dialog = StudyTimeDialog(subjects=self._repo.subjects(), deadlines=self._deadline_choices(deadline_id),
                                 default_start=start, title=title, deadline_id=deadline_id, subject=subject,
                                 parent=self)
        if self._exec_dialog(dialog):
            self._after_save(self._repo.add_study_time(dialog.result_item()))

    def _deadline_choices(self, keep_id: int | None = None) -> list[Deadline]:
        """Deadline chưa xong cho ô chọn; giữ thêm `keep_id` (deadline đang liên kết) kể cả khi nó đã xong."""
        choices = self._repo.open_deadlines()
        if keep_id is not None and all(d.id != keep_id for d in choices):
            if (kept := self._repo.get_deadline(keep_id)) is not None:
                choices.append(kept)
        return choices

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
        if isinstance(item, ClassSession):
            self.edit_course(item.course_id)  # buổi học sinh ra từ môn: sửa môn
            return
        if isinstance(item, Deadline):
            dialog = DeadlineDialog(files=self._files, subjects=self._repo.subjects(), deadline=item,
                                copy_default=self._settings.copy_attachments_to_app_dir,
                                subject_colors=self._repo.subject_colors(), parent=self)
            self._editing_ids.add(item_id)
            try:
                accepted = self._exec_dialog(dialog)
            finally:
                self._editing_ids.discard(item_id)
            if accepted:
                deadline = dialog.result_item()
                self._copy_files(deadline, dialog.copy_files.isChecked())
                self._save_subject_color(dialog)
                self._after_save(self._repo.update_deadline(deadline))
                self._cleanup_copies()  # bản sao của đính kèm vừa gỡ
        elif isinstance(item, StudyTime):
            dialog = StudyTimeDialog(subjects=self._repo.subjects(), study=item,
                                     deadlines=self._deadline_choices(item.deadline_id), parent=self)
            if self._exec_dialog(dialog):
                self._after_save(self._repo.update_study_time(dialog.result_item()))
        else:
            dialog = EventDialog(event=item, parent=self)
            if self._exec_dialog(dialog):
                self._after_save(self._repo.update_event(dialog.result_item()))

    @guarded
    def delete_item(self, item_id: int) -> None:
        item = self._repo.get_item(item_id)
        if item is None:
            return
        if isinstance(item, ClassSession):
            choice = prompts.ask_delete_class(self, item.title)
            if choice == "skip":
                self.skip_session(item_id)
            elif choice == "course":
                self._remove_course(item.course_id)
            return
        study_mode = "unlink"
        if isinstance(item, Deadline) and item.study_count:
            # Hỏi về các Study time liên kết thay cho hộp xác nhận thường: Xoá luôn / Giữ lại / Huỷ.
            study_mode = prompts.ask_delete_deadline_links(self, item.title, item.study_count)
            if study_mode == "cancel":
                return
        else:
            extra = ""
            if isinstance(item, Deadline) and item.attachments:
                extra = "\n\n" + tr("delete.extra_originals")
                if any(is_app_copy(a, paths.attachments_dir()) for a in item.attachments):
                    extra += " " + tr("delete.extra_copies")
            what = item.KIND if isinstance(item, (Deadline, StudyTime)) else "event"
            if not prompts.confirm(self, tr(f"delete.{what}_title"), tr(f"delete.{what}_text", title=item.title, extra=extra)):
                return
        if isinstance(item, Deadline):
            self._repo.delete_deadline(item_id, study_times=study_mode)
        else:
            self._repo.delete_item(item_id)  # xoá Study time không ảnh hưởng Deadline
        log.info("Deleted item #%d", item_id)
        self._cleanup_copies()
        self.refresh()

    @guarded
    def set_status(self, item_id: int, status: DeadlineStatus) -> None:
        deadline = self._repo.get_deadline(item_id)
        if deadline is None or deadline.status == status:
            return
        self._repo.set_deadline_status(item_id, status)
        self._refresh_keep_detail()

    # ------------------------------------------------------------------ Thời khoá biểu
    @guarded
    def new_course(self) -> None:
        dialog = CourseDialog(default_first_day=self._selected, subject_colors=self._repo.subject_colors(),
                              taken_names=[c.name for c in self._repo.list_courses()], parent=self)
        if self._exec_dialog(dialog):
            self._save_subject_color(dialog)
            course = self._repo.add_course(dialog.result_item())
            self.refresh()
            self.open_course(course.id)  # type: ignore[arg-type]

    @guarded
    def edit_course(self, course_id: int) -> None:
        course = self._repo.get_course(course_id)
        if course is None:
            self.refresh()
            return
        others = [c.name for c in self._repo.list_courses() if c.id != course_id]
        dialog = CourseDialog(course=course, subject_colors=self._repo.subject_colors(), taken_names=others,
                              parent=self)
        if self._exec_dialog(dialog):
            saved = self._repo.update_course(dialog.result_item())
            self._save_subject_color(dialog)  # sau update: đổi tên môn thì màu cũ đã được chuyển sang tên mới
            self.refresh()
            self.open_course(saved.id)  # type: ignore[arg-type]

    @guarded
    def delete_course(self, course_id: int) -> None:
        course = self._repo.get_course(course_id)
        if course is None:
            return
        if prompts.confirm(self, tr("delete.course_title"), tr("delete.course_text", title=course.name)):
            self._remove_course(course_id)

    def _remove_course(self, course_id: int) -> None:
        self._repo.delete_course(course_id)
        log.info("Deleted course #%d", course_id)
        self.refresh()

    @guarded
    def skip_session(self, item_id: int) -> None:
        """Nghỉ một buổi học (vd. ngày lễ): buổi đó biến khỏi lịch, các buổi khác giữ nguyên."""
        if self._repo.skip_session(item_id):
            log.info("Skipped class session #%d", item_id)
            self._refresh_keep_detail()

    @guarded
    def open_course(self, course_id: int) -> None:
        """Trang môn học ở panel phải: lịch học + Deadline / Study time cùng môn."""
        course = self._repo.get_course(course_id)
        if course is None:
            self.refresh()
            return
        now = self._clock()
        self.panel.show_course(
            course, now=now, next_session=self._repo.next_session(course_id, now),
            deadlines=self._repo.items_for_subject(course.name, Deadline.KIND),
            studies=self._repo.items_for_subject(course.name, StudyTime.KIND, since=now),
            colors=self._colors,
        )

    @guarded
    def show_course_menu(self, course_id: int, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(tr("menu.details"), lambda: self.open_course(course_id))
        menu.addAction(tr("menu.edit"), lambda: self.edit_course(course_id))
        menu.addSeparator()
        menu.addAction(tr("menu.delete"), lambda: self.delete_course(course_id))
        menu.exec(pos)

    def editing_deadline_ids(self) -> frozenset[int]:
        return frozenset(self._editing_ids)

    def after_auto_delete(self, count: int) -> None:
        """Vừa tự xoá `count` deadline: báo ở thanh trạng thái và làm mới (panel đang mở deadline đã xoá thì về danh sách)."""
        self.statusBar().showMessage(tr("autoclean.status", count=count), 10_000)
        self._refresh_keep_detail()

    def _auto_delete_remaining(self, item) -> timedelta | None:  # type: ignore[no-untyped-def]
        if not isinstance(item, Deadline):
            return None
        return time_until_delete(item, self._clock(), self._settings)

    def _after_save(self, item: Deadline | Event | StudyTime) -> None:
        """Sau khi lưu: nhảy tới ngày của mục và mở chi tiết của nó."""
        self._selected = item.starts_at.date()
        if self._nav not in LIST_NAVS:
            self._nav = self._nav_for_day(self._selected)
        self.refresh()
        self.open_item(item.id)  # type: ignore[arg-type]

    def _save_subject_color(self, dialog: DeadlineDialog | CourseDialog) -> None:
        if (choice := dialog.subject_color_choice()) is not None:
            self._repo.set_subject_color(*choice)

    def _copy_files(self, deadline: Deadline, copy_files: bool) -> None:
        errors = prepare_attachments(deadline.attachments, copy_files=copy_files, dest_dir=paths.attachments_dir())
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

    def _on_detail(self, action: Callable[[int], None], course_action: Callable[[int], None]) -> None:
        if (item_id := self.panel.detail_item_id()) is not None:
            action(item_id)
        elif (course_id := self.panel.course_id()) is not None:
            course_action(course_id)

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
        if isinstance(item, ClassSession):
            course_id = item.course_id
            menu.addAction(tr("menu.open_course"), lambda: self.open_course(course_id))
            menu.addAction(tr("menu.edit_course"), lambda: self.edit_course(course_id))
            menu.addSeparator()
            menu.addAction(tr("menu.skip_class"), lambda: self.skip_session(item_id))
            menu.addAction(tr("menu.delete_course"), lambda: self.delete_course(course_id))
            return menu
        if isinstance(item, Deadline) and len(item.file_attachments) == 1:
            att = item.file_attachments[0]
            menu.addAction(tr("menu.open_file", name=att.name), lambda: self.open_attachment(att))
        menu.addAction(tr("menu.edit"), lambda: self.edit_item(item_id))
        if isinstance(item, Deadline):
            if item.is_done:
                menu.addAction(tr("menu.mark_todo"), lambda: self.set_status(item_id, DeadlineStatus.TODO))
            else:
                menu.addAction(tr("menu.mark_done"), lambda: self.set_status(item_id, DeadlineStatus.DONE))
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
        self.sidebar.set_courses(self._repo.list_courses(), self._colors, current=self.panel.course_id())
        self._refresh_sidebar()
        self._refresh_panel()
        self._refresh_overlay()

    def _refresh_keep_detail(self) -> None:
        """Làm mới mọi thứ nhưng giữ trang chi tiết / trang môn học đang mở (nếu còn tồn tại)."""
        detail_id = self.panel.detail_item_id()
        course_id = self.panel.course_id()
        self.refresh()
        if detail_id is not None and self._repo.get_item(detail_id) is not None:
            self.open_item(detail_id)
        elif course_id is not None and self._repo.get_course(course_id) is not None:
            self.open_course(course_id)

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
            "overdue": len(self._repo.overdue_deadlines(now)),
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
                subtitle = tr("search.status_deadlines", count=count, status=status)
            self.panel.show_items(tr("search.title"), subtitle, items, now=now, today=self._today,
                                  group_by_day=True, colors=self._colors, empty_text=tr("search.empty"))
        elif self._nav == "upcoming":
            items = self._repo.upcoming(now)
            self.panel.show_items(tr("upcoming.title"), tr("upcoming.subtitle", count=len(items)), items,
                                  now=now, today=self._today, group_by_day=True, colors=self._colors,
                                  empty_text=tr("upcoming.empty"))
        elif self._nav == "overdue":
            items = self._repo.overdue_deadlines(now)
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
                if self._view != ViewMode.MONTH:
                    self._views[self._view].update_now(self._clock())  # vạch 'bây giờ' trên lưới giờ
            if (item_id := self.panel.detail_item_id()) is not None:   # dòng "Tự xoá sau …" đếm lùi theo giờ
                self.panel.detail.set_auto_delete_note(self._auto_delete_remaining(self._repo.get_item(item_id)))
        except Exception:
            log.exception("Lỗi khi cập nhật định kỳ")
