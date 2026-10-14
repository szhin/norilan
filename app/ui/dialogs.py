"""Dialog thêm/sửa Task và Event, kèm trình quản lý đính kèm (chọn file, kéo-thả, link)."""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.calendar_logic import REMIND_UNITS, split_minutes
from app.colors import DEFAULT_EVENT_TONE, ColorResolver, pick_tone
from app import paths
from app.files import AttachmentMissingError, FileService, attachment_from_target, is_app_copy, is_missing
from app.models import (
    Attachment,
    AttachmentKind,
    Event,
    Priority,
    Task,
    TaskStatus,
    ValidationError,
)
from app.ui import prompts
from app.ui.color_picker import ToneButton

ATTACHMENT_ICON = {AttachmentKind.FILE: "📄", AttachmentKind.FOLDER: "📁", AttachmentKind.URL: "🔗"}
_MISSING_COLOR = QColor("#d44c47")


# ------------------------------------------------------------------ Ô ngày + giờ
class DateTimeRow(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setMinimumWidth(84)
        layout.addWidget(self.date_edit, 3)
        layout.addWidget(self.time_edit, 2)
        layout.addStretch(1)
        self.date_edit.dateChanged.connect(self.changed)
        self.time_edit.timeChanged.connect(self.changed)

    def value(self) -> datetime:
        return datetime.combine(self.date_edit.date().toPython(), self.time_edit.time().toPython())

    def set_value(self, value: datetime) -> None:
        self.date_edit.setDate(QDate(value.year, value.month, value.day))
        self.time_edit.setTime(QTime(value.hour, value.minute))


# ------------------------------------------------------------------ Nhắc trước
class ReminderEdit(QWidget):
    """[x] Nhắc trước [ 30 ] [phút ▾] – trả về số phút hoặc None."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.enabled = QCheckBox("Nhắc trước")
        self.amount = QSpinBox()
        self.amount.setMinimumWidth(72)
        self.amount.setRange(0, 999)
        self.amount.setValue(30)
        self.unit = QComboBox()
        for label, minutes in REMIND_UNITS:
            self.unit.addItem(label, minutes)
        layout.addWidget(self.enabled)
        layout.addWidget(self.amount)
        layout.addWidget(self.unit)
        layout.addStretch(1)
        self.enabled.toggled.connect(self._sync)
        self._sync(False)

    def _sync(self, on: bool) -> None:
        self.amount.setEnabled(on)
        self.unit.setEnabled(on)

    def value(self) -> int | None:
        if not self.enabled.isChecked():
            return None
        return self.amount.value() * int(self.unit.currentData())

    def set_value(self, minutes: int | None) -> None:
        self.enabled.setChecked(minutes is not None)
        if minutes is not None:
            amount, unit = split_minutes(minutes)
            self.amount.setValue(amount)
            self.unit.setCurrentIndex(self.unit.findData(unit))


# ------------------------------------------------------------------ Đính kèm
class AttachmentEditor(QWidget):
    """Danh sách đính kèm có thể thêm (file/thư mục/link/kéo-thả), mở, đổi đường dẫn, xoá."""

    changed = Signal()

    def __init__(self, files: FileService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._files = files
        self._items: list[Attachment] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        for text, handler in (("📄 Thêm file…", self._add_files), ("📁 Thêm thư mục…", self._add_folder),
                              ("🔗 Thêm link…", self._add_link)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            add_row.addWidget(button)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        self.list = QListWidget(objectName="AttachmentList")
        self.list.setMinimumHeight(90)
        self.list.itemDoubleClicked.connect(lambda _: self.open_selected())
        self.list.currentRowChanged.connect(lambda _: self._sync_buttons())
        layout.addWidget(self.list)

        hint = QLabel("Mẹo: kéo-thả file, thư mục hoặc link vào cửa sổ này. Nhấp đúp để mở.", objectName="DropHint")
        layout.addWidget(hint)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._open_btn = QPushButton("Mở")
        self._open_btn.clicked.connect(self.open_selected)
        self._relink_btn = QPushButton("Đổi đường dẫn…")
        self._relink_btn.clicked.connect(self.relink_selected)
        self._remove_btn = QPushButton("Gỡ khỏi bài tập")
        self._remove_btn.setObjectName("DangerButton")
        self._remove_btn.clicked.connect(self.remove_selected)
        action_row.addWidget(self._open_btn)
        action_row.addWidget(self._relink_btn)
        action_row.addStretch(1)
        action_row.addWidget(self._remove_btn)
        layout.addLayout(action_row)
        self._sync_buttons()

    # --- dữ liệu
    def attachments(self) -> list[Attachment]:
        return [copy.copy(a) for a in self._items]

    def set_attachments(self, attachments: list[Attachment]) -> None:
        self._items = [copy.copy(a) for a in attachments]
        self._render()

    def file_count(self) -> int:
        return sum(1 for a in self._items if a.kind == AttachmentKind.FILE)

    def add_targets(self, targets: list[str]) -> int:
        """Thêm nhiều đường dẫn/URL; bỏ qua mục trống hoặc đã có. Trả về số mục đã thêm."""
        existing = {a.target.casefold() for a in self._items}
        added = 0
        for target in targets:
            try:
                att = attachment_from_target(target)
            except ValueError:
                continue
            if att.target.casefold() in existing:
                continue
            existing.add(att.target.casefold())
            self._items.append(att)
            added += 1
        if added:
            self._render(select=len(self._items) - 1)
        return added

    # --- thao tác
    def _add_files(self) -> None:
        self.add_targets(prompts.pick_files(self))

    def _add_folder(self) -> None:
        if folder := prompts.pick_folder(self):
            self.add_targets([folder])

    def _add_link(self) -> None:
        if link := prompts.ask_link(self):
            self.add_targets([link])

    def _current(self) -> Attachment | None:
        row = self.list.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def open_selected(self) -> None:
        att = self._current()
        if att is None:
            return
        try:
            self._files.open(att)
        except AttachmentMissingError:
            if prompts.ask_missing_action(self, att) == "relink":
                self.relink_selected()
        except OSError as exc:
            prompts.show_error(self, "Windows không mở được đính kèm này.", str(exc))

    def relink_selected(self) -> None:
        row = self.list.currentRow()
        att = self._current()
        if att is None or att.kind == AttachmentKind.URL:
            if att is not None and (link := prompts.ask_link(self)):
                att.target = attachment_from_target(link).target
                self._render(select=row)
            return
        if path := prompts.choose_replacement(self, att):
            replacement = attachment_from_target(path)
            att.target, att.kind = replacement.target, replacement.kind
            self._render(select=row)

    def remove_selected(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._items):
            del self._items[row]
            self._render(select=min(row, len(self._items) - 1))

    # --- hiển thị
    def _render(self, select: int | None = None) -> None:
        self.list.clear()
        for att in self._items:
            missing = is_missing(att)
            text = f"{ATTACHMENT_ICON[att.kind]}  {att.name}"
            if missing:
                text += "   — không tìm thấy"
            item = QListWidgetItem(text)
            item.setToolTip(att.target)
            if missing:
                item.setForeground(_MISSING_COLOR)
            self.list.addItem(item)
        if select is not None and select >= 0:
            self.list.setCurrentRow(select)
        self._sync_buttons()
        self.changed.emit()

    def _sync_buttons(self) -> None:
        has = self._current() is not None
        for button in (self._open_btn, self._relink_btn, self._remove_btn):
            button.setEnabled(has)


# ------------------------------------------------------------------ Cơ sở dialog
class _ItemDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{title} – {APP_NAME}")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(20, 18, 20, 16)
        self._root.setSpacing(12)

        self.title_edit = QLineEdit(objectName="TitleEdit")
        self._root.addWidget(self.title_edit)

        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(10)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._root.addLayout(self.form)

    def _finish(self, save_text: str) -> None:
        self.error_label = QLabel(objectName="ErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self._root.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Huỷ")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton(save_text, objectName="PrimaryButton")
        self.save_button.setDefault(True)
        self.save_button.setToolTip("Lưu (Ctrl+Enter)")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        self._root.addLayout(buttons)

        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self).activated.connect(self.accept)
        self.title_edit.textChanged.connect(self._sync_save)
        self._sync_save()
        self.title_edit.setFocus()

    def _sync_save(self) -> None:
        self.save_button.setEnabled(bool(self.title_edit.text().strip()))

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def accept(self) -> None:  # noqa: D401 – kiểm tra dữ liệu trước khi đóng
        try:
            self.result_item().validate()
        except ValidationError as exc:
            self._show_error(str(exc))
            return
        super().accept()

    def result_item(self) -> Task | Event:  # pragma: no cover – lớp con cài đặt
        raise NotImplementedError


# ------------------------------------------------------------------ Task
class TaskDialog(_ItemDialog):
    def __init__(
        self, *, files: FileService, subjects: list[str], task: Task | None = None,
        default_due: datetime | None = None, copy_default: bool = False,
        subject_colors: dict[str, str] | None = None, parent: QWidget | None = None,
    ) -> None:
        super().__init__("Sửa bài tập" if task else "Bài tập mới", parent)
        self._original = task
        self._subject_colors = dict(subject_colors or {})
        self._colors = ColorResolver("subject", self._subject_colors)
        self._new_subject_tone = pick_tone(self._subject_colors.values())  # màu sẽ gán nếu gõ môn mới
        self._picked_tones: dict[str, str] = {}  # môn → màu người dùng vừa chọn trong dialog này
        self.setAcceptDrops(True)
        self.resize(600, 680)
        self.title_edit.setPlaceholderText("Tên bài tập, vd. Lab 2 – Hàm trong Matlab")

        self.due = DateTimeRow()
        self.form.addRow("Hạn nộp", self.due)

        status_priority = QHBoxLayout()
        self.status = QComboBox()
        for status in TaskStatus:
            self.status.addItem(status.label, status)
        self.priority = QComboBox()
        for priority in Priority:
            self.priority.addItem(priority.label, priority)
        status_priority.addWidget(self.status, 1)
        status_priority.addSpacing(14)
        status_priority.addWidget(QLabel("Ưu tiên"))
        status_priority.addWidget(self.priority, 1)
        self.form.addRow("Trạng thái", status_priority)

        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.subject.addItems(subjects)
        self.subject.setCurrentText("")
        self.subject.lineEdit().setPlaceholderText("Môn học / tag, vd. Phương pháp tính")
        self.subject_tone = ToneButton()
        self.subject_tone.setToolTip("Màu của môn học này trên lịch (áp dụng cho mọi bài tập cùng môn)")
        self.subject_tone.tone_changed.connect(self._on_tone_picked)
        self.subject.currentTextChanged.connect(self._sync_subject_tone)
        subject_row = QHBoxLayout()
        subject_row.setSpacing(6)
        subject_row.addWidget(self.subject, 1)
        subject_row.addWidget(self.subject_tone)
        self.form.addRow("Môn học", subject_row)

        self.reminder = ReminderEdit()
        self.form.addRow("Nhắc việc", self.reminder)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Ghi chú, yêu cầu đề bài…")
        self.notes.setFixedHeight(80)
        self.form.addRow("Ghi chú", self.notes)

        self._root.addWidget(QLabel("ĐÍNH KÈM", objectName="SectionLabel"))
        self.attachments = AttachmentEditor(files)
        self._root.addWidget(self.attachments, 1)
        self.copy_files = QCheckBox("Lưu bản sao file vào Norilan – vẫn mở được khi file gốc bị xoá hoặc di chuyển")
        self.copy_files.setToolTip(
            "Khi lưu, mọi file đính kèm được chép vào thư mục riêng của Norilan và bài tập sẽ mở bản sao đó.\n"
            "Lưu ý: sửa bản sao không làm thay đổi file gốc (và ngược lại).\n"
            "Thư mục và link web không được sao chép."
        )
        self.copy_files.setChecked(copy_default)
        self._root.addWidget(self.copy_files)
        self.priority.setCurrentIndex(self.priority.findData(Priority.NORMAL))

        self._finish("Lưu bài tập" if task else "Tạo bài tập")
        if task is not None:
            self._load(task)
        else:
            self.due.set_value(default_due or datetime.combine(date.today() + timedelta(days=1), time(23, 59)))
        self._sync_subject_tone()

    def _load(self, task: Task) -> None:
        self.title_edit.setText(task.title)
        self.due.set_value(task.due_at)
        self.status.setCurrentIndex(self.status.findData(task.status))
        self.priority.setCurrentIndex(self.priority.findData(task.priority))
        self.subject.setCurrentText(task.subject)
        self.reminder.set_value(task.remind_before_minutes)
        self.notes.setPlainText(task.notes)
        self.attachments.set_attachments(task.attachments)
        # Bài tập đã có bản sao trong Norilan → giữ ô này được tích để file mới thêm cũng được sao chép.
        if any(is_app_copy(a, paths.attachments_dir()) for a in task.attachments):
            self.copy_files.setChecked(True)
        self.title_edit.deselect()
        self.title_edit.end(False)

    def _tone_for(self, subject: str) -> str:
        if subject in self._picked_tones:
            return self._picked_tones[subject]
        if subject in self._subject_colors:
            return self._colors.subject_tone(subject)
        return self._new_subject_tone if subject else "gray"

    def _sync_subject_tone(self) -> None:
        subject = self.subject.currentText().strip()
        self.subject_tone.setEnabled(bool(subject))
        self.subject_tone.set_tone(self._tone_for(subject))

    def _on_tone_picked(self, tone: str) -> None:
        if subject := self.subject.currentText().strip():
            self._picked_tones[subject] = tone

    def subject_color_choice(self) -> tuple[str, str] | None:
        """(môn, màu) cần lưu sau khi bấm Lưu: màu vừa chọn, hoặc màu cho môn mới gõ lần đầu."""
        subject = self.subject.currentText().strip()
        if subject and (subject in self._picked_tones or subject not in self._subject_colors):
            return subject, self._tone_for(subject)
        return None

    def result_item(self) -> Task:
        original = self._original
        return Task(
            title=self.title_edit.text().strip(),
            due_at=self.due.value(),
            status=self.status.currentData(),
            priority=self.priority.currentData(),
            subject=self.subject.currentText().strip(),
            notes=self.notes.toPlainText().strip(),
            remind_before_minutes=self.reminder.value(),
            attachments=self.attachments.attachments(),
            id=original.id if original else None,
        )

    # --- kéo-thả
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls():
            targets = [u.toLocalFile() if u.isLocalFile() else u.toString() for u in mime.urls()]
        else:
            targets = [line for line in mime.text().splitlines() if line.strip()]
        if self.attachments.add_targets(targets):
            event.acceptProposedAction()


# ------------------------------------------------------------------ Event
class EventDialog(_ItemDialog):
    def __init__(
        self, *, event: Event | None = None, default_start: datetime | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Sửa sự kiện" if event else "Sự kiện mới", parent)
        self._original = event
        self.title_edit.setPlaceholderText("Tên sự kiện, vd. Họp nhóm đồ án")
        self._duration = timedelta(hours=1)

        self.start = DateTimeRow()
        self.end = DateTimeRow()
        self.form.addRow("Bắt đầu", self.start)
        self.form.addRow("Kết thúc", self.end)
        self.location = QLineEdit()
        self.location.setPlaceholderText("Phòng học, địa chỉ hoặc link họp online")
        self.form.addRow("Địa điểm", self.location)
        self.color = ToneButton(DEFAULT_EVENT_TONE)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color)
        color_row.addStretch(1)
        self.form.addRow("Màu", color_row)
        self.reminder = ReminderEdit()
        self.form.addRow("Nhắc việc", self.reminder)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Ghi chú…")
        self.notes.setFixedHeight(90)
        self.form.addRow("Ghi chú", self.notes)
        self._finish("Lưu sự kiện" if event else "Tạo sự kiện")
        self.resize(520, self.sizeHint().height())

        if event is not None:
            self.title_edit.setText(event.title)
            self.start.set_value(event.starts_at)
            self.end.set_value(event.ends_at)
            self._duration = event.ends_at - event.starts_at
            self.location.setText(event.location)
            self.color.set_tone(event.color or DEFAULT_EVENT_TONE)
            self.reminder.set_value(event.remind_before_minutes)
            self.notes.setPlainText(event.notes)
        else:
            start = default_start or datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            self.start.set_value(start)
            self.end.set_value(start + self._duration)
        # Đổi giờ bắt đầu thì giờ kết thúc dời theo, giữ nguyên thời lượng.
        self.start.changed.connect(self._on_start_changed)
        self.end.changed.connect(self._on_end_changed)

    def _on_start_changed(self) -> None:
        self.end.blockSignals(True)
        self.end.set_value(self.start.value() + self._duration)
        self.end.blockSignals(False)

    def _on_end_changed(self) -> None:
        if self.end.value() >= self.start.value():
            self._duration = self.end.value() - self.start.value()

    def result_item(self) -> Event:
        original = self._original
        return Event(
            title=self.title_edit.text().strip(),
            starts_at=self.start.value(),
            ends_at=self.end.value(),
            location=self.location.text().strip(),
            notes=self.notes.toPlainText().strip(),
            remind_before_minutes=self.reminder.value(),
            color=self.color.tone(),
            id=original.id if original else None,
        )
