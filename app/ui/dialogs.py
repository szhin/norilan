"""Dialog thêm/sửa Deadline, Study time, Event và môn học, kèm trình quản lý đính kèm (chọn file, kéo-thả, link)."""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
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
from app.calendar_logic import REMIND_UNITS, date_text, day_month_text, split_minutes, weekday_short
from app.colors import DEFAULT_EVENT_TONE, ColorResolver, pick_tone
from app import paths
from app.db import fold_text
from app.files import AttachmentMissingError, FileService, attachment_from_target, is_app_copy, is_missing
from app.i18n import tr
from app.models import (
    Attachment,
    AttachmentKind,
    Course,
    Event,
    Exam,
    Priority,
    Deadline,
    DeadlineStatus,
    StudyTime,
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
        self.date_edit.setDisplayFormat(tr("dialog.date_format"))
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
        self.enabled = QCheckBox(tr("reminder.enable"))
        self.amount = QSpinBox()
        self.amount.setMinimumWidth(72)
        self.amount.setRange(0, 999)
        self.amount.setValue(30)
        self.unit = QComboBox()
        for key, minutes in REMIND_UNITS:
            self.unit.addItem(tr(f"remind.unit.{key}"), minutes)
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
        for text, handler in ((tr("attach.add_file"), self._add_files), (tr("attach.add_folder"), self._add_folder),
                              (tr("attach.add_link"), self._add_link)):
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

        hint = QLabel(tr("attach.hint"), objectName="DropHint")
        layout.addWidget(hint)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._open_btn = QPushButton(tr("common.open"))
        self._open_btn.clicked.connect(self.open_selected)
        self._relink_btn = QPushButton(tr("attach.relink"))
        self._relink_btn.clicked.connect(self.relink_selected)
        self._remove_btn = QPushButton(tr("attach.remove"))
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
            prompts.show_error(self, tr("attach.open_error"), str(exc))

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
                text += f"   {tr('attach.missing_suffix')}"
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
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton(save_text, objectName="PrimaryButton")
        self.save_button.setDefault(True)
        self.save_button.setToolTip(tr("dialog.save_tooltip"))
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

    def result_item(self) -> Deadline | Event | StudyTime | Exam:  # pragma: no cover – lớp con cài đặt
        raise NotImplementedError


# ------------------------------------------------------------------ Deadline
class DeadlineDialog(_ItemDialog):
    def __init__(
        self, *, files: FileService, subjects: list[str], deadline: Deadline | None = None,
        default_due: datetime | None = None, copy_default: bool = False,
        subject_colors: dict[str, str] | None = None, subject: str = "", parent: QWidget | None = None,
    ) -> None:
        super().__init__(tr("deadline.window_edit" if deadline else "deadline.window_new"), parent)
        self._original = deadline
        self._subject_colors = dict(subject_colors or {})
        self._colors = ColorResolver("subject", self._subject_colors)
        self._new_subject_tone = pick_tone(self._subject_colors.values())  # màu sẽ gán nếu gõ môn mới
        self._picked_tones: dict[str, str] = {}  # môn → màu người dùng vừa chọn trong dialog này
        self.setAcceptDrops(True)
        self.resize(600, 680)
        self.title_edit.setPlaceholderText(tr("deadline.placeholder"))

        self.due = DateTimeRow()
        self.form.addRow(tr("deadline.due"), self.due)

        status_priority = QHBoxLayout()
        self.status = QComboBox()
        for status in DeadlineStatus:
            self.status.addItem(status.label, status)
        self.priority = QComboBox()
        for priority in Priority:
            self.priority.addItem(priority.label, priority)
        status_priority.addWidget(self.status, 1)
        status_priority.addSpacing(14)
        status_priority.addWidget(QLabel(tr("deadline.priority")))
        status_priority.addWidget(self.priority, 1)
        self.form.addRow(tr("deadline.status"), status_priority)

        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.subject.addItems(subjects)
        self.subject.setCurrentText("")
        self.subject.lineEdit().setPlaceholderText(tr("deadline.subject_placeholder"))
        self.subject_tone = ToneButton()
        self.subject_tone.setToolTip(tr("deadline.subject_color_tooltip"))
        self.subject_tone.tone_changed.connect(self._on_tone_picked)
        self.subject.currentTextChanged.connect(self._sync_subject_tone)
        subject_row = QHBoxLayout()
        subject_row.setSpacing(6)
        subject_row.addWidget(self.subject, 1)
        subject_row.addWidget(self.subject_tone)
        self.form.addRow(tr("deadline.subject"), subject_row)

        self.reminder = ReminderEdit()
        self.form.addRow(tr("deadline.remind"), self.reminder)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("deadline.notes_placeholder"))
        self.notes.setFixedHeight(80)
        self.form.addRow(tr("deadline.notes"), self.notes)

        self._root.addWidget(QLabel(tr("deadline.attachments"), objectName="SectionLabel"))
        self.attachments = AttachmentEditor(files)
        self._root.addWidget(self.attachments, 1)
        self.copy_files = QCheckBox(tr("deadline.copy_files"))
        self.copy_files.setToolTip(tr("deadline.copy_tooltip"))
        self.copy_files.setChecked(copy_default)
        self._root.addWidget(self.copy_files)
        self.priority.setCurrentIndex(self.priority.findData(Priority.NORMAL))

        self._finish(tr("deadline.save_edit" if deadline else "deadline.save_new"))
        if deadline is not None:
            self._load(deadline)
        else:
            self.due.set_value(default_due or datetime.combine(date.today() + timedelta(days=1), time(23, 59)))
            self.subject.setCurrentText(subject)  # mở từ trang môn học: điền sẵn môn
        self._sync_subject_tone()

    def _load(self, deadline: Deadline) -> None:
        self.title_edit.setText(deadline.title)
        self.due.set_value(deadline.due_at)
        self.status.setCurrentIndex(self.status.findData(deadline.status))
        self.priority.setCurrentIndex(self.priority.findData(deadline.priority))
        self.subject.setCurrentText(deadline.subject)
        self.reminder.set_value(deadline.remind_before_minutes)
        self.notes.setPlainText(deadline.notes)
        self.attachments.set_attachments(deadline.attachments)
        # Bài tập đã có bản sao trong Norilan → giữ ô này được tích để file mới thêm cũng được sao chép.
        if any(is_app_copy(a, paths.attachments_dir()) for a in deadline.attachments):
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

    def result_item(self) -> Deadline:
        original = self._original
        return Deadline(
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
        super().__init__(tr("event.window_edit" if event else "event.window_new"), parent)
        self._original = event
        self.title_edit.setPlaceholderText(tr("event.placeholder"))
        self._duration = timedelta(hours=1)

        self.start = DateTimeRow()
        self.end = DateTimeRow()
        self.form.addRow(tr("event.start"), self.start)
        self.form.addRow(tr("event.end"), self.end)
        self.location = QLineEdit()
        self.location.setPlaceholderText(tr("event.location_placeholder"))
        self.form.addRow(tr("event.location"), self.location)
        self.color = ToneButton(DEFAULT_EVENT_TONE)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color)
        color_row.addStretch(1)
        self.form.addRow(tr("event.color"), color_row)
        self.reminder = ReminderEdit()
        self.form.addRow(tr("deadline.remind"), self.reminder)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("event.notes_placeholder"))
        self.notes.setFixedHeight(90)
        self.form.addRow(tr("event.notes"), self.notes)
        self._finish(tr("event.save_edit" if event else "event.save_new"))
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


# ------------------------------------------------------------------ Ca thi
class ExamDialog(_ItemDialog):
    """Tạo/sửa ca thi: môn thi (ô tên), ngày + giờ bắt đầu, lớp thi, địa điểm thi."""

    def __init__(
        self, *, exam: Exam | None = None, default_start: datetime | None = None, subjects: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tr("exam.window_edit" if exam else "exam.window_new"), parent)
        self._original = exam
        self.title_edit.setPlaceholderText(tr("exam.placeholder"))
        if subjects:
            self.title_edit.setCompleter(QCompleter(subjects, self.title_edit))

        self.start = DateTimeRow()
        self.form.addRow(tr("exam.start"), self.start)
        self.exam_class = QLineEdit()
        self.exam_class.setPlaceholderText(tr("exam.class_placeholder"))
        self.form.addRow(tr("exam.class"), self.exam_class)
        self.location = QLineEdit()
        self.location.setPlaceholderText(tr("exam.location_placeholder"))
        self.form.addRow(tr("exam.location"), self.location)
        self.reminder = ReminderEdit()
        self.form.addRow(tr("deadline.remind"), self.reminder)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("exam.notes_placeholder"))
        self.notes.setFixedHeight(80)
        self.form.addRow(tr("exam.notes"), self.notes)
        self._finish(tr("exam.save_edit" if exam else "exam.save_new"))
        self.resize(520, self.sizeHint().height())

        if exam is not None:
            self.title_edit.setText(exam.title)
            self.start.set_value(exam.starts_at)
            self.exam_class.setText(exam.exam_class)
            self.location.setText(exam.location)
            self.reminder.set_value(exam.remind_before_minutes)
            self.notes.setPlainText(exam.notes)
        else:
            self.start.set_value(default_start or datetime.combine(date.today() + timedelta(days=1), time(7, 30)))
            self.reminder.set_value(60)   # ca thi mới: nhắc trước 1 giờ (bỏ tích được)

    def result_item(self) -> Exam:
        original = self._original
        return Exam(
            title=self.title_edit.text().strip(),
            starts_at=self.start.value(),
            exam_class=self.exam_class.text().strip(),
            location=self.location.text().strip(),
            notes=self.notes.toPlainText().strip(),
            remind_before_minutes=self.reminder.value(),
            id=original.id if original else None,
        )


# ------------------------------------------------------------------ Study time
class StudyTimeDialog(_ItemDialog):
    """Thêm/sửa Study time. Ô Deadline (tuỳ chọn) chỉ liệt kê các deadline chưa xong do người gọi truyền vào;
    chọn deadline thì môn học được điền sẵn theo deadline đó (nếu người dùng chưa tự nhập môn khác)."""

    def __init__(
        self, *, subjects: list[str], deadlines: list[Deadline], study: StudyTime | None = None,
        default_start: datetime | None = None, title: str = "", deadline_id: int | None = None,
        subject: str = "", parent: QWidget | None = None,
    ) -> None:
        super().__init__(tr("study.window_edit" if study else "study.window_new"), parent)
        self._original = study
        self._deadlines = {d.id: d for d in deadlines}
        self._auto_subject = ""   # môn do dialog tự điền theo deadline (để biết khi nào được phép ghi đè)
        self._duration = timedelta(hours=1)
        self.title_edit.setPlaceholderText(tr("study.placeholder"))

        self.deadline = QComboBox()
        self.deadline.setToolTip(tr("study.deadline_tooltip"))
        self.deadline.addItem(tr("study.deadline_none"), None)
        for d in deadlines:
            self.deadline.addItem(f"{d.title}  ({day_month_text(d.due_at)} {d.due_at:%H:%M})", d.id)
        self.form.addRow(tr("study.deadline"), self.deadline)

        self.start = DateTimeRow()
        self.end = DateTimeRow()
        self.form.addRow(tr("study.start"), self.start)
        self.form.addRow(tr("study.end"), self.end)

        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.subject.addItems(subjects)
        self.subject.setCurrentText("")
        self.subject.lineEdit().setPlaceholderText(tr("study.subject_placeholder"))
        self.form.addRow(tr("study.subject"), self.subject)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("study.notes_placeholder"))
        self.notes.setFixedHeight(90)
        self.form.addRow(tr("study.notes"), self.notes)
        self._finish(tr("study.save_edit" if study else "study.save_new"))
        self.resize(540, self.sizeHint().height())

        if study is not None:
            self.title_edit.setText(study.title)
            self.start.set_value(study.starts_at)
            self.end.set_value(study.ends_at)
            self._duration = study.ends_at - study.starts_at
            self.subject.setCurrentText(study.subject)
            self.notes.setPlainText(study.notes)
            self._select_deadline(study.deadline_id)
        else:
            start = default_start or datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            self.start.set_value(start)
            self.end.set_value(start + self._duration)
            self.title_edit.setText(title)  # sao chép một lần từ deadline, không đồng bộ về sau
            self.subject.setCurrentText(subject)
            self._select_deadline(deadline_id)
            self._fill_subject_from_deadline()
        self.deadline.currentIndexChanged.connect(lambda _: self._fill_subject_from_deadline())
        self.start.changed.connect(self._on_start_changed)
        self.end.changed.connect(self._on_end_changed)
        self.title_edit.end(False)

    def _select_deadline(self, deadline_id: int | None) -> None:
        index = self.deadline.findData(deadline_id)
        self.deadline.setCurrentIndex(max(index, 0))

    def _fill_subject_from_deadline(self) -> None:
        deadline = self._deadlines.get(self.deadline.currentData())
        current = self.subject.currentText().strip()
        if deadline is not None and deadline.subject and current in ("", self._auto_subject):
            self.subject.setCurrentText(deadline.subject)
            self._auto_subject = deadline.subject

    def _on_start_changed(self) -> None:
        self.end.blockSignals(True)
        self.end.set_value(self.start.value() + self._duration)
        self.end.blockSignals(False)

    def _on_end_changed(self) -> None:
        if self.end.value() > self.start.value():
            self._duration = self.end.value() - self.start.value()

    def result_item(self) -> StudyTime:
        original = self._original
        return StudyTime(
            title=self.title_edit.text().strip(),
            starts_at=self.start.value(),
            ends_at=self.end.value(),
            subject=self.subject.currentText().strip(),
            notes=self.notes.toPlainText().strip(),
            deadline_id=self.deadline.currentData(),
            remind_before_minutes=original.remind_before_minutes if original else None,
            id=original.id if original else None,
        )


# ------------------------------------------------------------------ Môn học (thời khoá biểu)
class CourseDialog(_ItemDialog):
    """Thêm/sửa môn học lặp lại hằng tuần: tên, thứ, giờ học, phòng, địa điểm, ngày bắt đầu, số tuần.

    `taken_names`: tên các môn khác (không tính môn đang sửa) – trùng tên thì báo lỗi ngay trong dialog.
    """

    MAX_WEEKS = 52

    def __init__(
        self, *, course: Course | None = None, default_first_day: date | None = None,
        subject_colors: dict[str, str] | None = None, taken_names: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tr("course.window_edit" if course else "course.window_new"), parent)
        self._original = course
        self._taken = {fold_text(n.strip()) for n in taken_names or []}
        self._subject_colors = dict(subject_colors or {})
        self._colors = ColorResolver("subject", self._subject_colors)
        self._new_tone = pick_tone(self._subject_colors.values())
        self._picked_tone: str | None = None
        self._duration = timedelta(hours=2)
        self.title_edit.setPlaceholderText(tr("course.placeholder"))

        days = QHBoxLayout()
        days.setSpacing(4)
        self.day_buttons: list[QPushButton] = []
        for index in range(7):
            button = QPushButton(weekday_short(index), objectName="DayToggle")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.toggled.connect(lambda _: self._on_changed())
            days.addWidget(button)
            self.day_buttons.append(button)
        days.addStretch(1)
        self.form.addRow(tr("course.weekdays"), days)

        self.start_time = QTimeEdit()
        self.end_time = QTimeEdit()
        time_row = QHBoxLayout()
        time_row.setSpacing(6)
        for edit in (self.start_time, self.end_time):
            edit.setDisplayFormat("HH:mm")
            edit.setMinimumWidth(84)
        time_row.addWidget(self.start_time)
        time_row.addWidget(QLabel("–"))
        time_row.addWidget(self.end_time)
        time_row.addStretch(1)
        self.form.addRow(tr("course.time"), time_row)

        self.room = QLineEdit()
        self.room.setPlaceholderText(tr("course.room_placeholder"))
        self.form.addRow(tr("course.room"), self.room)
        self.location = QLineEdit()
        self.location.setPlaceholderText(tr("course.location_placeholder"))
        self.form.addRow(tr("course.location"), self.location)

        self.first_day = QDateEdit()
        self.first_day.setCalendarPopup(True)
        self.first_day.setDisplayFormat(tr("dialog.date_format"))
        self.form.addRow(tr("course.first_day"), self.first_day)

        self.weeks = QSpinBox()
        self.weeks.setRange(1, self.MAX_WEEKS)
        self.weeks.setSuffix(tr("course.weeks_suffix"))
        self.weeks.setMinimumWidth(96)
        self.until = QLabel(objectName="Muted")
        weeks_row = QHBoxLayout()
        weeks_row.setSpacing(10)
        weeks_row.addWidget(self.weeks)
        weeks_row.addWidget(self.until, 1)
        self.form.addRow(tr("course.weeks"), weeks_row)

        self.tone = ToneButton()
        self.tone.setToolTip(tr("course.color_tooltip"))
        self.tone.tone_changed.connect(self._on_tone_picked)
        tone_row = QHBoxLayout()
        tone_row.addWidget(self.tone)
        tone_row.addStretch(1)
        self.form.addRow(tr("course.color"), tone_row)

        self.reminder = ReminderEdit()
        self.form.addRow(tr("deadline.remind"), self.reminder)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("course.notes_placeholder"))
        self.notes.setFixedHeight(70)
        self.form.addRow(tr("event.notes"), self.notes)
        hint = QLabel(tr("course.hint"), objectName="DropHint")
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        self._finish(tr("course.save_edit" if course else "course.save_new"))
        if course is not None:
            self.title_edit.setText(course.name)
            for day in course.weekdays:
                self.day_buttons[day].setChecked(True)
            self.start_time.setTime(QTime(course.start_time.hour, course.start_time.minute))
            self.end_time.setTime(QTime(course.end_time.hour, course.end_time.minute))
            self._duration = datetime.combine(date.min, course.end_time) - datetime.combine(date.min, course.start_time)
            self.room.setText(course.room)
            self.location.setText(course.location)
            self.first_day.setDate(QDate(course.first_day.year, course.first_day.month, course.first_day.day))
            self.weeks.setValue(course.weeks)
            self.reminder.set_value(course.remind_before_minutes)
            self.notes.setPlainText(course.notes)
        else:
            first = default_first_day or date.today()
            self.day_buttons[first.weekday()].setChecked(True)
            self.start_time.setTime(QTime(7, 30))
            self.end_time.setTime(QTime(9, 30))
            self.first_day.setDate(QDate(first.year, first.month, first.day))
            self.weeks.setValue(15)
        self.resize(560, self.sizeHint().height())
        self.title_edit.textChanged.connect(lambda _: self._sync_tone())
        self.start_time.timeChanged.connect(self._on_start_changed)
        self.end_time.timeChanged.connect(self._on_end_changed)
        self.first_day.dateChanged.connect(lambda _: self._on_changed())
        self.weeks.valueChanged.connect(lambda _: self._on_changed())
        self._sync_tone()
        self._on_changed()

    # --- giờ học: đổi giờ bắt đầu thì giờ kết thúc dời theo, giữ nguyên thời lượng
    def _on_start_changed(self) -> None:
        start = datetime.combine(date.min, self.start_time.time().toPython())
        end = start + self._duration
        if end.date() == start.date():
            self.end_time.blockSignals(True)
            self.end_time.setTime(QTime(end.hour, end.minute))
            self.end_time.blockSignals(False)

    def _on_end_changed(self) -> None:
        start, end = self.start_time.time().toPython(), self.end_time.time().toPython()
        if end > start:
            self._duration = datetime.combine(date.min, end) - datetime.combine(date.min, start)

    def selected_days(self) -> tuple[int, ...]:
        return tuple(i for i, button in enumerate(self.day_buttons) if button.isChecked())

    def _on_changed(self) -> None:
        """Cập nhật dòng 'buổi cuối …' và nút Lưu (cần tên + ít nhất một thứ)."""
        days = self.selected_days()
        if days:
            course = self._course(days)
            self.until.setText(tr("course.until", date=date_text(course.last_day), count=len(course.session_dates())))
        else:
            self.until.setText("")
        self._sync_save()

    def _sync_save(self) -> None:
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(bool(self.title_edit.text().strip()) and bool(self.selected_days()))

    # --- màu môn (dùng chung với Deadline / Study time cùng môn)
    def _tone_for(self, name: str) -> str:
        if self._picked_tone is not None:
            return self._picked_tone
        if name in self._subject_colors:
            return self._colors.subject_tone(name)
        if self._original is not None and self._original.name in self._subject_colors:
            return self._colors.subject_tone(self._original.name)  # đổi tên môn: màu đi theo
        return self._new_tone if name else "gray"

    def _sync_tone(self) -> None:
        name = self.title_edit.text().strip()
        self.tone.setEnabled(bool(name))
        self.tone.set_tone(self._tone_for(name))

    def _on_tone_picked(self, tone: str) -> None:
        self._picked_tone = tone

    def subject_color_choice(self) -> tuple[str, str] | None:
        """(môn, màu) cần lưu sau khi bấm Lưu: màu vừa chọn, hoặc màu cho tên môn chưa có màu."""
        name = self.title_edit.text().strip()
        if name and (self._picked_tone is not None or name not in self._subject_colors):
            return name, self._tone_for(name)
        return None

    def accept(self) -> None:
        if fold_text(self.title_edit.text().strip()) in self._taken:
            self._show_error(tr("error.course_exists", name=self.title_edit.text().strip()))
            return
        super().accept()

    def _course(self, days: tuple[int, ...]) -> Course:
        original = self._original
        return Course(
            name=self.title_edit.text().strip(),
            weekdays=days,
            start_time=self.start_time.time().toPython(),
            end_time=self.end_time.time().toPython(),
            first_day=self.first_day.date().toPython(),
            weeks=self.weeks.value(),
            room=self.room.text().strip(),
            location=self.location.text().strip(),
            notes=self.notes.toPlainText().strip(),
            remind_before_minutes=self.reminder.value(),
            skipped=original.skipped if original else (),
            id=original.id if original else None,
        )

    def result_item(self) -> Course:  # type: ignore[override]
        return self._course(self.selected_days())
