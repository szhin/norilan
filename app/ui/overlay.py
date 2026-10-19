"""Overlay đếm ngược toàn cửa sổ cho một Study time (chỉ mở khi người dùng bấm đồng hồ nhỏ)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from app.calendar_logic import time_left_text, time_text
from app.countdown import Phase, format_hms, state as countdown_state
from app.i18n import tr
from app.models import Deadline, StudyTime
from app.ui.theme import is_dark, repolish

PROGRESS_STEPS = 1000


class StudyOverlay(QWidget):
    """Widget con phủ kín cửa sổ chính: nền mờ + thẻ ở giữa. Chặn mọi thao tác phía dưới khi đang mở.

    Không có đồng hồ riêng: người gọi gọi `update_state(now)` mỗi tick của timer 1 giây dùng chung.
    Đóng bằng Esc, nút X hoặc bấm vào nền. Hết giờ khi đang mở thì hiện "Đã kết thúc" và giữ nguyên.
    """

    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("CountdownOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.study_id: int | None = None
        self._study: StudyTime | None = None
        self._deadline: Deadline | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        middle = QHBoxLayout()
        middle.addStretch(1)
        self.card = QFrame(objectName="OverlayCard")
        self.card.setMinimumWidth(560)
        self.card.setMaximumWidth(860)
        middle.addWidget(self.card, 10)
        middle.addStretch(1)
        outer.addLayout(middle)
        outer.addStretch(1)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(36, 14, 36, 32)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.addStretch(1)
        self.close_button = QPushButton("✕", objectName="OverlayClose")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip(tr("overlay.close_tooltip"))
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self.close_overlay)
        top.addWidget(self.close_button)
        layout.addLayout(top)

        self.caption = QLabel(objectName="OverlayCaption")
        self.digits = QLabel(objectName="OverlayDigits")
        self.title = QLabel(objectName="OverlayTitle")
        self.range = QLabel(objectName="OverlayRange")
        self.deadline = QLabel(objectName="OverlayDeadline")
        self.progress = QProgressBar(objectName="OverlayProgress")
        self.progress.setRange(0, PROGRESS_STEPS)
        self.progress.setTextVisible(False)
        for label in (self.caption, self.digits, self.title, self.range, self.deadline):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.deadline.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.caption)
        layout.addWidget(self.digits)
        layout.addSpacing(6)
        layout.addWidget(self.title)
        layout.addWidget(self.range)
        layout.addWidget(self.deadline)
        layout.addSpacing(12)
        layout.addWidget(self.progress)

        parent.installEventFilter(self)   # theo kích thước cửa sổ chính
        self.hide()

    # --- API
    def is_open(self) -> bool:
        return self.study_id is not None and not self.isHidden()

    def show_for(self, study: StudyTime, deadline: Deadline | None, now: datetime) -> None:
        """Mở (hoặc nạp lại dữ liệu nếu đang mở) cho một Study time."""
        was_open = self.is_open()
        self._study, self._deadline, self.study_id = study, deadline, study.id
        self.title.setText(study.title)
        self.range.setText(time_text(study))
        self.update_state(now)
        self.setGeometry(self.parentWidget().rect())
        if not was_open:
            self.show()
            self.raise_()
            self.setFocus()

    def close_overlay(self) -> None:
        if self.study_id is None and self.isHidden():
            return
        self.hide()
        self.study_id = None
        self._study = self._deadline = None
        self.closed.emit()

    def update_state(self, now: datetime) -> None:
        study = self._study
        if study is None:
            return
        st = countdown_state(now, study.starts_at, study.ends_at)
        if st.phase == Phase.ENDED:
            self.caption.setText("")
            self.digits.setText(tr("countdown.ended"))
        else:
            self.caption.setText(tr("overlay.caption_upcoming" if st.phase == Phase.UPCOMING
                                    else "overlay.caption_running"))
            self.digits.setText(format_hms(st.seconds))
        self.caption.setVisible(st.phase != Phase.ENDED)
        self.progress.setValue(round(st.fraction * PROGRESS_STEPS))
        warn = st.is_warning
        for widget in (self.digits, self.progress):
            if widget.property("phase") != st.phase.value or widget.property("warn") != warn:
                widget.setProperty("phase", st.phase.value)
                widget.setProperty("warn", warn)
                repolish(widget)
        deadline = self._deadline
        if deadline is None:
            self.deadline.hide()
        else:
            text = tr("overlay.deadline", title=deadline.title)
            if left := time_left_text(deadline, now):
                text += f" · {left}"
            self.deadline.setText(text)
            self.deadline.show()

    # --- sự kiện
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 175) if is_dark() else QColor(20, 20, 20, 120))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.card.geometry().contains(event.position().toPoint()):
            self.close_overlay()
        event.accept()   # không để click lọt xuống giao diện bên dưới

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
        event.accept()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and not self.isHidden():
            self.setGeometry(self.parentWidget().rect())
        return False

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
