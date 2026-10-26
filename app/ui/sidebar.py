"""Sidebar trái: Lịch, Hôm nay, Ngày mai, Sắp tới, Quá hạn – kèm số đếm; bên dưới là thời khoá biểu."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, __version__
from app.calendar_logic import course_schedule_text, place_text
from app.colors import ColorResolver
from app.i18n import tr
from app.models import Course
from app.ui.theme import apply_tone, repolish
from app.ui.widgets import ElidedLabel

# (khoá điều hướng, biểu tượng); chữ hiển thị lấy từ bảng dịch 'nav.<khoá>'
NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("calendar", "📅"),
    ("today", "☀️"),
    ("tomorrow", "🌤️"),
    ("upcoming", "🗓️"),
    ("exams", "🎓"),
    ("overdue", "⏰"),
)


class NavButton(QPushButton):
    def __init__(self, icon: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(8)
        self._text = QLabel(f"{icon}  {text}", objectName="NavText")
        self._badge = QLabel(objectName="NavBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.hide()
        for child in (self._text, self._badge):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._badge)

    def set_count(self, count: int, *, danger: bool = False) -> None:
        self._badge.setText(str(count))
        self._badge.setVisible(count > 0)
        if self._badge.property("danger") != danger:
            self._badge.setProperty("danger", danger)
            repolish(self._badge)

    def count_text(self) -> str:
        return self._badge.text() if not self._badge.isHidden() else ""


class CourseButton(QPushButton):
    """Một môn trong thời khoá biểu: thanh màu môn + tên + 'T2, T4 · 07:30 – 09:30'."""

    context_requested = Signal(int, QPoint)

    def __init__(self, course: Course, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CourseButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.course_id: int = course.id  # type: ignore[assignment]
        schedule = course_schedule_text(course)
        place = place_text(course.room, course.location)
        self.setToolTip("\n".join(p for p in (course.name, schedule, place) if p))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(8)
        bar = QFrame(objectName="KindBar")
        bar.setFixedWidth(3)
        apply_tone(bar, tone, vertical=True)
        column = QVBoxLayout()
        column.setSpacing(0)
        self.name = ElidedLabel(course.name)
        self.name.setObjectName("CourseName")
        meta = ElidedLabel(schedule)
        meta.setObjectName("CourseMeta")
        column.addWidget(self.name)
        column.addWidget(meta)
        layout.addWidget(bar)
        layout.addLayout(column, 1)
        for child in (bar, self.name, meta):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            child.setToolTip("")

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_requested.emit(self.course_id, event.globalPos())
        event.accept()


class Sidebar(QFrame):
    nav_changed = Signal(str)
    new_deadline_requested = Signal()
    new_exam_requested = Signal()
    settings_requested = Signal()
    search_changed = Signal(str)
    new_course_requested = Signal()
    course_clicked = Signal(int)
    course_context = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(8, 0, 0, 6)
        header.setSpacing(8)
        self.logo = QLabel(objectName="AppLogo")
        self.logo.setFixedSize(22, 22)
        header.addWidget(self.logo)
        title = QLabel(APP_NAME, objectName="AppTitle")
        title.setStyleSheet("padding: 0px;")
        header.addWidget(title, 1)
        layout.addLayout(header)

        self.search = QLineEdit(objectName="SearchBox")
        self.search.setPlaceholderText(tr("sidebar.search_placeholder"))
        self.search.setToolTip(tr("sidebar.search_tooltip"))
        self.search.setClearButtonEnabled(True)
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # không tự nhận con trỏ khi mở app (Ctrl+F vẫn được)
        self.search.textChanged.connect(self.search_changed)
        clear = QShortcut(QKeySequence("Esc"), self.search)
        clear.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear.activated.connect(self.search.clear)
        layout.addWidget(self.search)
        layout.addSpacing(4)

        new_deadline = QPushButton(tr("sidebar.new_deadline"), objectName="SidebarAction")
        new_deadline.setCursor(Qt.CursorShape.PointingHandCursor)
        new_deadline.setToolTip(tr("sidebar.new_deadline_tooltip"))
        new_deadline.clicked.connect(self.new_deadline_requested)
        layout.addWidget(new_deadline)
        new_exam = QPushButton(tr("sidebar.new_exam"), objectName="SidebarAction")
        new_exam.setCursor(Qt.CursorShape.PointingHandCursor)
        new_exam.setToolTip(tr("sidebar.new_exam_tooltip"))
        new_exam.clicked.connect(self.new_exam_requested)
        layout.addWidget(new_exam)
        layout.addWidget(QLabel(tr("sidebar.section"), objectName="SidebarSection"))

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, NavButton] = {}
        for key, icon in NAV_ITEMS:
            button = NavButton(icon, tr(f"nav.{key}"))
            button.clicked.connect(lambda _=False, k=key: self.nav_changed.emit(k))
            self._group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button

        layout.addWidget(self._build_courses(), 1)
        settings = QPushButton(tr("sidebar.settings"), objectName="SidebarAction")
        settings.setCursor(Qt.CursorShape.PointingHandCursor)
        settings.setToolTip(tr("sidebar.settings_tooltip"))
        settings.clicked.connect(self.settings_requested)
        layout.addWidget(settings)
        layout.addWidget(QLabel(f"{APP_NAME} {__version__}", objectName="SidebarFooter"))
        self.set_current("calendar")

    def _build_courses(self) -> QWidget:
        """Mục 'THỜI KHOÁ BIỂU': tiêu đề + nút ＋, danh sách môn cuộn được (chiếm phần trống còn lại)."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 4, 0)
        header.addWidget(QLabel(tr("sidebar.courses"), objectName="SidebarSection"), 1)
        add = QPushButton("＋", objectName="IconButton")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setToolTip(tr("sidebar.add_course_tooltip"))
        add.clicked.connect(self.new_course_requested)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self._course_list = QVBoxLayout(inner)
        self._course_list.setContentsMargins(0, 0, 0, 0)
        self._course_list.setSpacing(2)
        area.setWidget(inner)
        layout.addWidget(area, 1)
        self.course_buttons: list[CourseButton] = []
        self.add_course_button: QPushButton | None = None
        self.set_courses([])
        return box

    def set_courses(self, courses: list[Course], colors: ColorResolver | None = None,
                    current: int | None = None) -> None:
        """Vẽ lại danh sách môn; chưa có môn nào thì hiện nút '＋ Thêm môn học' viền đứt."""
        colors = colors or ColorResolver()
        while (child := self._course_list.takeAt(0)) is not None:
            if (widget := child.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        self.course_buttons = []
        self.add_course_button = None
        if not courses:
            self.add_course_button = QPushButton(tr("sidebar.add_course"), objectName="AddStudyButton")
            self.add_course_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.add_course_button.setToolTip(tr("sidebar.add_course_tooltip"))
            self.add_course_button.clicked.connect(self.new_course_requested)
            self._course_list.addWidget(self.add_course_button)
        for course in courses:
            button = CourseButton(course, colors.subject_tone(course.name))
            button.setChecked(course.id == current)
            button.clicked.connect(lambda _=False, i=course.id: self.course_clicked.emit(i))
            button.context_requested.connect(self.course_context)
            self._course_list.addWidget(button)
            self.course_buttons.append(button)
        self._course_list.addStretch(1)

    def set_current_course(self, course_id: int | None) -> None:
        for button in self.course_buttons:
            button.setChecked(button.course_id == course_id)

    def set_logo(self, pixmap) -> None:  # type: ignore[no-untyped-def]
        self.logo.setPixmap(pixmap)

    def set_current(self, key: str) -> None:
        """Đánh dấu mục đang chọn mà không phát tín hiệu ('search' = không mục nào được chọn)."""
        if key in self.buttons:
            self.buttons[key].setChecked(True)
            return
        self._group.setExclusive(False)
        for button in self.buttons.values():
            button.setChecked(False)
        self._group.setExclusive(True)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def set_counts(self, counts: dict[str, int]) -> None:
        for key, value in counts.items():
            if key in self.buttons:
                self.buttons[key].set_count(value, danger=key in ("overdue", "exams"))
