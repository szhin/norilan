"""Dải đỏ trên cùng cửa sổ: ca thi gần nhất luôn hiện; nút bên phải xổ xuống toàn bộ lịch thi."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.calendar_logic import exam_banner_text, exam_days_left
from app.i18n import tr
from app.models import Exam
from app.ui.widgets import ElidedButton

LIST_MAX_HEIGHT = 240   # danh sách xổ xuống cao hơn mức này thì cuộn


class ExamBanner(QFrame):
    exam_clicked = Signal(int)   # exam_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExamBanner")
        self._expanded = False
        self._exams: list[Exam] = []
        self._now: datetime | None = None
        self.rows: list[ElidedButton] = []   # mọi dòng: dòng đầu (ca thi gần nhất) rồi các dòng xổ xuống

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 8, 18, 8)
        outer.setSpacing(4)
        self._head = QHBoxLayout()
        self._head.setSpacing(12)
        outer.addLayout(self._head)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setMaximumHeight(LIST_MAX_HEIGHT)
        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 6, 0)
        self._list.setSpacing(2)
        self._area.setWidget(inner)
        self._area.hide()
        outer.addWidget(self._area)

        self.toggle = QPushButton(objectName="ExamToggle")
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.clicked.connect(lambda: self.set_expanded(not self._expanded))
        self.hide()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded and len(self._exams) > 1
        self._sync()

    def set_exams(self, exams: list[Exam], now: datetime) -> None:
        """Vẽ lại theo toàn bộ ca thi sắp tới (đã sắp theo thời gian); rỗng thì ẩn cả dải."""
        self._exams, self._now = exams, now
        for layout in (self._head, self._list):
            while (child := layout.takeAt(0)) is not None:
                if (widget := child.widget()) is not None and widget is not self.toggle:
                    widget.hide()
                    widget.deleteLater()
        self.rows = []
        if not exams:
            self.toggle.hide()
            self._area.hide()
            self.hide()
            return
        for index, exam in enumerate(exams):
            row = self._row(exam, now)
            if index == 0:
                self._head.addWidget(row, 1)
            else:
                self._list.addWidget(row)
            self.rows.append(row)
        self._head.addWidget(self.toggle)
        self._list.addStretch(1)
        self._expanded = self._expanded and len(exams) > 1
        self._sync()
        self.show()

    def _row(self, exam: Exam, now: datetime) -> ElidedButton:
        row = ElidedButton(exam_banner_text(exam, now))
        row.setObjectName("ExamRow")
        row.setProperty("today", exam_days_left(exam, now) <= 0)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setToolTip(tr("exam.banner_tooltip"))
        row.clicked.connect(lambda _=False, i=exam.id: self.exam_clicked.emit(i))
        return row

    def _sync(self) -> None:
        many = len(self._exams) > 1
        self.toggle.setVisible(many)
        self.toggle.setText(tr("exam.banner_collapse") if self._expanded
                            else tr("exam.banner_expand", count=len(self._exams)))
        self._area.setVisible(self._expanded and many)

    def texts(self) -> list[str]:
        """Chữ đầy đủ của mọi dòng, kể cả dòng đang thu gọn (dùng cho test)."""
        return [row.fullText() for row in self.rows]
