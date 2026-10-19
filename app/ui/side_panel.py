"""Panel bên phải: danh sách mục (của một ngày hoặc một mục nhanh) và trang chi tiết mục."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QSizePolicy,
    QSplitter,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.countdown import CountdownState, Phase, format_hms, state as countdown_state
from app.calendar_logic import (
    chip_state, day_heading, day_month_text, format_minutes, item_meta, kind_icon, time_left_text, time_text,
)
from app.colors import ColorResolver
from app.files import is_missing
from app.i18n import tr
from app.models import Attachment, AttachmentKind, CalendarItem, Deadline, DeadlineStatus, StudyTime
from app.ui.dialogs import ATTACHMENT_ICON
from app.ui.item_list import ItemListWidget
from app.ui.theme import apply_tone, repolish
from app.ui.widgets import ElidedButton, ElidedLabel


# khoá trường (nhãn lấy từ bảng dịch 'field.<khoá>') → biểu tượng
_FIELD_ICONS = {
    "due": "🗓", "status": "◉", "priority": "⚑", "subject": "🏷", "remind": "⏰",
    "notes": "≡", "attachments": "📎", "time": "🕒", "location": "📍",
}


class AttachmentRow(QFrame):
    """Một đính kèm trong trang chi tiết: bấm tên để mở, nút 📂 để mở thư mục chứa."""

    open_requested = Signal(int)
    reveal_requested = Signal(int)

    def __init__(self, att: Attachment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Attachment")
        self.attachment_id: int = att.id  # type: ignore[assignment]
        self.missing = is_missing(att)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 4, 3)
        layout.setSpacing(4)

        text = f"{ATTACHMENT_ICON[att.kind]}  {att.name}" + (f"  {tr('attach.missing_suffix')}" if self.missing else "")
        self.open_button = ElidedButton(text)
        self.open_button.setObjectName("AttachmentOpen")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setProperty("missing", self.missing)
        self.open_button.setToolTip(
            tr("attach.tooltip_missing" if self.missing else "attach.tooltip_open", target=att.target)
        )
        self.open_button.clicked.connect(lambda: self.open_requested.emit(self.attachment_id))
        layout.addWidget(self.open_button, 1)

        if att.kind != AttachmentKind.URL:
            reveal = QPushButton("📂", objectName="IconButton")
            reveal.setCursor(Qt.CursorShape.PointingHandCursor)
            reveal.setToolTip(tr("attach.reveal_tooltip"))
            reveal.clicked.connect(lambda: self.reveal_requested.emit(self.attachment_id))
            layout.addWidget(reveal)


class _ClickFrame(QFrame):
    """Khung bấm được (thẻ Deadline liên kết)."""

    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class CountdownWidget(_ClickFrame):
    """Đồng hồ đếm ngược nhỏ của một Study time: bấm để phóng to (overlay). Chỉ hiển thị, không tự chạy:
    người gọi gọi `update_state(now)` mỗi tick (giờ lấy từ đồng hồ hệ thống, không có bộ đếm riêng)."""

    def __init__(self, study: StudyTime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CountdownTimer")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("countdown.enlarge_tooltip"))
        self.study_id = study.id
        self._start, self._end = study.starts_at, study.ends_at
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self._text = QLabel(objectName="CountdownText")
        layout.addWidget(self._text)
        layout.addStretch(1)
        self.last_state: CountdownState | None = None

    def update_state(self, now: datetime) -> CountdownState:
        st = countdown_state(now, self._start, self._end)
        hms = format_hms(st.seconds)
        if st.phase == Phase.UPCOMING:
            text = tr("countdown.upcoming", time=hms)
        elif st.phase == Phase.ENDED:
            text = tr("countdown.ended")
        else:
            text = f"⏱ {hms}"
        self._text.setText(text)
        warn = st.is_warning
        if self._text.property("phase") != st.phase.value or self._text.property("warn") != warn:
            self._text.setProperty("phase", st.phase.value)
            self._text.setProperty("warn", warn)
            repolish(self._text)
        self.last_state = st
        return st


class DetailView(QWidget):
    """Chi tiết một Deadline/Study time/Event, kèm nút Sửa / Xoá / đổi trạng thái và đính kèm bấm để mở.

    Deadline: panel chia đôi theo chiều dọc – trên là chi tiết, dưới là các Study time liên kết.
    Study time: chi tiết + thẻ Deadline liên kết (nếu có) + `countdown_slot` (khung trống, chỉ có ở Study time).
    """

    back_requested = Signal()
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    status_requested = Signal(int, object)      # item_id, DeadlineStatus
    open_attachment_requested = Signal(int)     # attachment_id
    reveal_attachment_requested = Signal(int)   # attachment_id
    add_study_requested = Signal(int)           # deadline_id: thêm Study time điền sẵn từ deadline này
    open_item_requested = Signal(int)           # mở một mục liên kết (Study time / Deadline)
    countdown_clicked = Signal(int)             # study_id: phóng to đồng hồ đếm ngược

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        top = QHBoxLayout()
        back = QPushButton(tr("detail.back"), objectName="LinkButton")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setToolTip(tr("detail.back_tooltip"))
        back.clicked.connect(self.back_requested)
        top.addWidget(back)
        top.addStretch(1)
        self.edit_button = QPushButton(tr("detail.edit"))
        self.edit_button.setToolTip(tr("detail.edit_tooltip"))
        self.edit_button.clicked.connect(lambda: self.item_id and self.edit_requested.emit(self.item_id))
        self.delete_button = QPushButton("🗑", objectName="DangerButton")
        self.delete_button.setToolTip(tr("detail.delete_tooltip"))
        self.delete_button.clicked.connect(lambda: self.item_id and self.delete_requested.emit(self.item_id))
        top.addWidget(self.edit_button)
        top.addWidget(self.delete_button)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 4, 6, 0)
        self._body.setSpacing(10)

        self._pill = QLabel(objectName="Pill")
        self._title = QLabel(objectName="DetailTitle")
        self._title.setWordWrap(True)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.addWidget(self._pill, 0, Qt.AlignmentFlag.AlignLeft)
        self._body.addWidget(self._title)

        form_host = QWidget()
        self._form = QFormLayout(form_host)
        self._form.setContentsMargins(0, 6, 0, 0)
        self._form.setHorizontalSpacing(14)
        self._form.setVerticalSpacing(10)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self._body.addWidget(form_host)
        self._extras = QVBoxLayout()   # thẻ Deadline liên kết + countdown_slot (chỉ Study time)
        self._extras.setContentsMargins(0, 0, 0, 0)
        self._extras.setSpacing(8)
        self._body.addLayout(self._extras)
        self._body.addStretch(1)
        scroll.setWidget(body)

        self._split = QSplitter(Qt.Orientation.Vertical)
        self._split.setChildrenCollapsible(False)
        self._split.setHandleWidth(1)
        self._split.addWidget(scroll)
        self._study_box = self._build_study_section()
        self._split.addWidget(self._study_box)
        self._split.setStretchFactor(0, 3)
        self._split.setStretchFactor(1, 2)
        self._study_box.hide()
        outer.addWidget(self._split, 1)
        self.attachment_rows: list[AttachmentRow] = []
        self.study_rows: list[ElidedButton] = []
        self.add_study_button: QPushButton | None = None
        self.countdown_slot: QWidget | None = None
        self.countdown_widget: CountdownWidget | None = None
        self.linked_card: QFrame | None = None

    def _build_study_section(self) -> QWidget:
        box = QWidget(objectName="StudySection")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self._study_header = QLabel(objectName="SectionLabel")
        header.addWidget(self._study_header, 1)
        self.add_study_icon = QPushButton("＋", objectName="IconButton")
        self.add_study_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_study_icon.setToolTip(tr("panel.add_study"))
        self.add_study_icon.clicked.connect(self._emit_add_study)
        header.addWidget(self.add_study_icon)
        layout.addLayout(header)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self._study_list = QVBoxLayout(inner)
        self._study_list.setContentsMargins(0, 0, 6, 0)
        self._study_list.setSpacing(4)
        area.setWidget(inner)
        layout.addWidget(area, 1)
        return box

    def _emit_add_study(self) -> None:
        if self.item_id is not None:
            self.add_study_requested.emit(self.item_id)

    def _fill_study_section(self, studies: list[StudyTime]) -> None:
        """Danh sách Study time liên kết (bấm để mở); chưa có thì hiện nút '+ Thêm Study time'."""
        while (child := self._study_list.takeAt(0)) is not None:
            if (widget := child.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        self.study_rows = []
        self.add_study_button = None
        self._study_header.setText(tr("panel.study_section", count=len(studies)))
        if not studies:
            self._study_list.addWidget(QLabel(tr("panel.study_none"), objectName="StudyEmpty"))
            self.add_study_button = QPushButton(tr("panel.add_study"), objectName="AddStudyButton")
            self.add_study_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.add_study_button.clicked.connect(self._emit_add_study)
            self._study_list.addWidget(self.add_study_button)
        for study in studies:
            when = f"{day_month_text(study.starts_at)} {study.starts_at:%H:%M} – {study.ends_at:%H:%M}"
            row = ElidedButton(f"📖  {when}   {study.title}")
            row.setObjectName("StudyLink")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setToolTip(tr("panel.open_study_tooltip"))
            row.clicked.connect(lambda _=False, i=study.id: self.open_item_requested.emit(i))
            self._study_list.addWidget(row)
            self.study_rows.append(row)
        self._study_list.addStretch(1)

    def _clear_extras(self) -> None:
        while (child := self._extras.takeAt(0)) is not None:
            if (widget := child.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        self.countdown_slot = None
        self.countdown_widget = None
        self.linked_card = None

    def update_countdown(self, now: datetime) -> None:
        if self.countdown_widget is not None:
            self.countdown_widget.update_state(now)

    def _linked_deadline_card(self, deadline: Deadline, now: datetime) -> QFrame:
        card = _ClickFrame(objectName="LinkedCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setToolTip(tr("panel.open_deadline_tooltip"))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        title = ElidedLabel(f"{kind_icon(deadline)}  {deadline.title}")
        title.setObjectName("CardTitle")
        meta = ElidedLabel(item_meta(deadline, now, with_date=True, show_subject=False))
        meta.setObjectName("CardMeta")
        meta.setProperty("state", chip_state(deadline, now))
        layout.addWidget(title)
        layout.addWidget(meta)
        card.clicked.connect(lambda i=deadline.id: self.open_item_requested.emit(i))
        return card

    def show_item(
        self, item: CalendarItem, now: datetime, colors: ColorResolver | None = None, *,
        linked_deadline: Deadline | None = None, study_times: list[StudyTime] | None = None,
    ) -> None:
        colors = colors or ColorResolver()
        self.item_id = item.id
        apply_tone(self._pill, colors.tone(item))
        self._pill.setText(tr(f"kind.{item.KIND}"))
        self._title.setText(item.title)
        self.attachment_rows = []
        while self._form.rowCount():
            self._form.removeRow(0)
        self._clear_extras()
        self._study_box.setVisible(isinstance(item, Deadline))
        if isinstance(item, Deadline):
            self._fill_study_section(study_times or [])
            self._split.setSizes([3, 2])

        if isinstance(item, Deadline):
            due_text = f"{item.due_at:%H:%M} · {day_heading(item.due_at.date())}"
            if left := time_left_text(item, now):
                due_text += f" · {left}"
            state = chip_state(item, now)
            self._row("due", due_text, state=state if state in ("overdue", "soon") else None)
            self._form.addRow(self._label("status"), self._status_box(item))
            self._row("priority", item.priority.label)
            if item.subject:
                pill = QLabel(item.subject, objectName="Pill")
                apply_tone(pill, colors.subject_tone(item.subject))
                pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self._form.addRow(self._label("subject"), pill)
            else:
                self._row("subject", "—")
            self._row("remind", format_minutes(item.remind_before_minutes))
            self._row("notes", item.notes or "—")
            self._form.addRow(self._label("attachments"), self._attachments(item))
        elif isinstance(item, StudyTime):
            self._row("time", f"{time_text(item)} · {day_heading(item.starts_at.date())}")
            if item.subject:
                pill = QLabel(item.subject, objectName="Pill")
                apply_tone(pill, colors.subject_tone(item.subject))
                pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self._form.addRow(self._label("subject"), pill)
            else:
                self._row("subject", "—")
            self._row("notes", item.notes or "—")
            if linked_deadline is not None:
                self._extras.addWidget(QLabel(tr("panel.linked_deadline"), objectName="SectionLabel"))
                self.linked_card = self._linked_deadline_card(linked_deadline, now)
                self._extras.addWidget(self.linked_card)
            # Đếm ngược nhỏ nằm trong `countdown_slot`; chỉ tồn tại ở Study time – không bao giờ ở Deadline/Event.
            self.countdown_slot = QWidget(objectName="countdown_slot")
            slot_layout = QVBoxLayout(self.countdown_slot)
            slot_layout.setContentsMargins(0, 0, 0, 0)
            self.countdown_widget = CountdownWidget(item)
            self.countdown_widget.update_state(now)
            self.countdown_widget.clicked.connect(lambda i=item.id: self.countdown_clicked.emit(i))
            slot_layout.addWidget(self.countdown_widget)
            self._extras.addWidget(self.countdown_slot)
        else:
            self._row("time", f"{time_text(item)} · {day_heading(item.starts_at.date())}")
            self._row("location", item.location or "—")
            self._row("remind", format_minutes(item.remind_before_minutes))
            self._row("notes", item.notes or "—")

    def field_values(self) -> dict[str, str]:
        """{nhãn đã dịch: giá trị} của các dòng chữ đang hiển thị (dùng cho test)."""
        values: dict[str, str] = {}
        for row in range(self._form.rowCount()):
            label = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
            field = self._form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget()
            if isinstance(label, QLabel) and isinstance(field, QLabel):
                values[label.property("field")] = field.text()
        return values

    # --- dựng từng phần
    def _label(self, key: str) -> QLabel:
        """Nhãn thuộc tính có biểu tượng phía trước, như bảng thuộc tính của Notion."""
        text = tr(f"field.{key}")
        label = QLabel(f"{_FIELD_ICONS.get(key, '•')}   {text}", objectName="FieldLabel")
        label.setProperty("field", text)
        return label

    def _row(self, key: str, value: str, *, state: str | None = None) -> None:
        field = QLabel(value, objectName="FieldValue")
        field.setWordWrap(True)
        field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if state:
            field.setProperty("state", state)
        self._form.addRow(self._label(key), field)

    def _status_box(self, deadline: Deadline) -> QWidget:
        box = QComboBox()
        for status in DeadlineStatus:
            box.addItem(status.label, status)
        box.setCurrentIndex(box.findData(deadline.status))
        deadline_id = deadline.id
        box.currentIndexChanged.connect(lambda _: self.status_requested.emit(deadline_id, box.currentData()))
        self.status_box = box
        return box

    def _attachments(self, deadline: Deadline) -> QWidget:
        if not deadline.attachments:
            return QLabel(tr("attach.none"), objectName="Muted")
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for att in deadline.attachments:
            row = AttachmentRow(att)
            row.open_requested.connect(self.open_attachment_requested)
            row.reveal_requested.connect(self.reveal_attachment_requested)
            layout.addWidget(row)
            self.attachment_rows.append(row)
        return box


class SidePanel(QFrame):
    """Trang 0: danh sách; trang 1: chi tiết."""

    item_clicked = Signal(int)
    item_context = Signal(int, QPoint)
    page_changed = Signal()                 # đổi giữa trang danh sách và trang chi tiết
    status_filter_changed = Signal(object)  # DeadlineStatus | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 12, 12)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        list_page = QWidget()
        lp = QVBoxLayout(list_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self._title = QLabel(objectName="PanelTitle")
        self._title.setWordWrap(True)
        lp.addWidget(self._title)
        self._subtitle = QLabel(objectName="PanelSubtitle")
        self._subtitle.setWordWrap(True)
        lp.addWidget(self._subtitle)
        # Bộ lọc trạng thái – chỉ hiện khi đang tìm kiếm.
        self.filter_row = QWidget(objectName="FilterRow")
        fr = QHBoxLayout(self.filter_row)
        fr.setContentsMargins(0, 6, 0, 0)
        fr.addWidget(QLabel(tr("panel.status"), objectName="Muted"))
        self.status_filter = QComboBox()
        self.status_filter.addItem(tr("panel.all"), None)
        for status in DeadlineStatus:
            self.status_filter.addItem(status.label, status)
        self.status_filter.currentIndexChanged.connect(
            lambda _: self.status_filter_changed.emit(self.status_filter.currentData()))
        fr.addWidget(self.status_filter, 1)
        self.filter_row.hide()
        lp.addWidget(self.filter_row)
        lp.addSpacing(8)
        self.list = ItemListWidget()
        self.list.item_clicked.connect(self.item_clicked)
        self.list.item_context.connect(self.item_context)
        lp.addWidget(self.list, 1)
        self._stack.addWidget(list_page)

        self.detail = DetailView()
        self.detail.back_requested.connect(self.show_list_page)
        self._stack.addWidget(self.detail)

    # --- API
    def show_items(
        self, title: str, subtitle: str, items: list[CalendarItem], *,
        now: datetime, today: date, group_by_day: bool = False, empty_text: str | None = None,
        colors: ColorResolver | None = None,
    ) -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        self.list.set_items(items, now=now, today=today, group_by_day=group_by_day,
                            empty_text=empty_text, colors=colors)
        self.show_list_page()

    def show_detail(
        self, item: CalendarItem, now: datetime, colors: ColorResolver | None = None, *,
        linked_deadline: Deadline | None = None, study_times: list[StudyTime] | None = None,
    ) -> None:
        self.detail.show_item(item, now, colors, linked_deadline=linked_deadline, study_times=study_times)
        self.list.set_selected(item.id)
        self._stack.setCurrentWidget(self.detail)
        self.page_changed.emit()

    def show_list_page(self) -> None:
        self.list.set_selected(None)
        self._stack.setCurrentIndex(0)
        self.page_changed.emit()

    def set_filter_visible(self, visible: bool) -> None:
        self.filter_row.setVisible(visible)

    def reset_filter(self) -> None:
        self.status_filter.blockSignals(True)
        self.status_filter.setCurrentIndex(0)
        self.status_filter.blockSignals(False)

    def subtitle(self) -> str:
        return self._subtitle.text()

    def is_showing_detail(self) -> bool:
        return self._stack.currentWidget() is self.detail

    def detail_item_id(self) -> int | None:
        return self.detail.item_id if self.is_showing_detail() else None

    def title(self) -> str:
        return self._title.text()
