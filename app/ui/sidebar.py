"""Sidebar trái: Lịch, Hôm nay, Ngày mai, Sắp tới, Quá hạn – kèm số đếm."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app import APP_NAME, __version__
from app.ui.theme import repolish

NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("calendar", "📅", "Lịch"),
    ("today", "☀️", "Hôm nay"),
    ("tomorrow", "🌤️", "Ngày mai"),
    ("upcoming", "🗓️", "Sắp tới (7 ngày)"),
    ("overdue", "⏰", "Quá hạn"),
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


class Sidebar(QFrame):
    nav_changed = Signal(str)
    new_task_requested = Signal()
    settings_requested = Signal()
    search_changed = Signal(str)

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
        self.search.setPlaceholderText("🔍  Tìm kiếm…")
        self.search.setToolTip("Tìm theo tiêu đề, ghi chú, môn học, địa điểm – không cần gõ dấu (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # không tự nhận con trỏ khi mở app (Ctrl+F vẫn được)
        self.search.textChanged.connect(self.search_changed)
        clear = QShortcut(QKeySequence("Esc"), self.search)
        clear.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear.activated.connect(self.search.clear)
        layout.addWidget(self.search)
        layout.addSpacing(4)

        new_task = QPushButton("＋  Bài tập mới", objectName="SidebarAction")
        new_task.setCursor(Qt.CursorShape.PointingHandCursor)
        new_task.setToolTip("Thêm bài tập (Ctrl+N)")
        new_task.clicked.connect(self.new_task_requested)
        layout.addWidget(new_task)
        layout.addWidget(QLabel("XEM NHANH", objectName="SidebarSection"))

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, NavButton] = {}
        for key, icon, text in NAV_ITEMS:
            button = NavButton(icon, text)
            button.clicked.connect(lambda _=False, k=key: self.nav_changed.emit(k))
            self._group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button

        layout.addStretch(1)
        settings = QPushButton("⚙️  Cài đặt", objectName="SidebarAction")
        settings.setCursor(Qt.CursorShape.PointingHandCursor)
        settings.setToolTip("Cài đặt (Ctrl+,)")
        settings.clicked.connect(self.settings_requested)
        layout.addWidget(settings)
        layout.addWidget(QLabel(f"{APP_NAME} {__version__}", objectName="SidebarFooter"))
        self.set_current("calendar")

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
                self.buttons[key].set_count(value, danger=key == "overdue")
