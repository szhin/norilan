"""Thêm deadline từ ảnh: chọn ảnh → Claude đọc → xem trước, sửa, bỏ chọn → lưu.

Dialog không biết gì về mạng hay DB: nhận `extract(images)` và `is_duplicate(title, due)` từ bên ngoài
(MainWindow), nên test có thể thay bằng hàm giả và chạy đồng bộ qua `runner`.
"""
from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.ai_import import GEMINI_KEY_URL, MAX_IMAGES, AiImportError, ExtractedDeadline, ImageData, match_subject
from app.i18n import tr
from app.models import Deadline
from app.ui import prompts
from app.ui.dialogs import DateTimeRow, ReminderEdit

log = logging.getLogger(__name__)

MAX_SIDE = 1800                    # ảnh lớn hơn được thu nhỏ trước khi gửi (nhanh hơn, rẻ hơn, chữ vẫn đọc được)
MAX_BYTES = 4_500_000              # API giới hạn 5 MB/ảnh; vượt thì chuyển sang JPEG
THUMB = QSize(120, 84)

_ALIVE: set[QThread] = set()       # giữ luồng đang chạy kể cả khi dialog đã đóng, tránh bị huỷ giữa chừng


def encode_image(image: QImage) -> ImageData:
    """QImage → PNG (giữ chữ sắc nét); quá nặng thì JPEG."""
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    for media_type, fmt, quality in (("image/png", "PNG", -1), ("image/jpeg", "JPG", 85)):
        flat = image.convertToFormat(QImage.Format.Format_RGB32) if fmt == "JPG" else image
        array = QByteArray()
        buffer = QBuffer(array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        flat.save(buffer, fmt, quality)
        buffer.close()
        if len(array) <= MAX_BYTES or fmt == "JPG":
            return ImageData(media_type, bytes(array))
    raise AssertionError("unreachable")  # pragma: no cover


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, job: Callable[[], object]) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            self.done.emit(self._job())
        except AiImportError as exc:
            self.failed.emit(exc)
        except Exception as exc:  # noqa: BLE001 – mọi lỗi lạ đều phải quay về UI, không được làm sập luồng
            log.exception("AI import failed")
            self.failed.emit(AiImportError("server", repr(exc)))


def thread_runner(job: Callable[[], object], on_done: Callable, on_error: Callable) -> None:
    worker = _Worker(job)
    _ALIVE.add(worker)
    worker.done.connect(on_done)
    worker.failed.connect(on_error)
    worker.finished.connect(lambda: (_ALIVE.discard(worker), worker.deleteLater()))
    worker.start()


def sync_runner(job: Callable[[], object], on_done: Callable, on_error: Callable) -> None:
    """Chạy ngay trên luồng gọi – dùng trong test."""
    try:
        result = job()
    except AiImportError as exc:
        on_error(exc)
    else:
        on_done(result)


@dataclass(slots=True)
class ImportEntry:
    """Một dòng người dùng đã chọn: deadline (kèm nhắc trước) và, nếu họ bấm đặt, một khung Study time."""

    deadline: Deadline
    study: tuple[datetime, datetime] | None = None


class StudySlotDialog(QDialog):
    """Hộp nhỏ để đặt (hoặc xoá) giờ Study time cho một dòng. Study time thật chỉ được tạo sau khi deadline được lưu."""

    def __init__(self, title: str, start: datetime, end: datetime, *, has_value: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{tr('aiimport.study_window')} – {APP_NAME}")
        self.cleared = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        heading = QLabel(title, objectName="DetailTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.start = DateTimeRow()
        self.end = DateTimeRow()
        self.start.set_value(start)
        self.end.set_value(end)
        self._duration = end - start
        for label, row in ((tr("study.start"), self.start), (tr("study.end"), self.end)):
            line = QHBoxLayout()
            caption = QLabel(label)
            caption.setMinimumWidth(110)
            line.addWidget(caption)
            line.addWidget(row, 1)
            layout.addLayout(line)
        self.start.changed.connect(self._on_start_changed)
        self.end.changed.connect(self._on_end_changed)
        self.error_label = QLabel(objectName="ErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.clear_button = QPushButton(tr("aiimport.study_clear"))
        self.clear_button.setEnabled(has_value)
        self.clear_button.clicked.connect(self._clear)
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("common.save"), objectName="PrimaryButton")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def _on_start_changed(self) -> None:      # giữ nguyên độ dài khi dời giờ bắt đầu (như hộp Study time thường)
        self.end.blockSignals(True)
        self.end.set_value(self.start.value() + self._duration)
        self.end.blockSignals(False)

    def _on_end_changed(self) -> None:
        if self.end.value() > self.start.value():
            self._duration = self.end.value() - self.start.value()

    def _clear(self) -> None:
        self.cleared = True
        super().accept()

    def accept(self) -> None:
        if self.end.value() <= self.start.value():
            self.error_label.setText(tr("error.end_before_start"))
            self.error_label.show()
            return
        super().accept()

    def value(self) -> tuple[datetime, datetime] | None:
        return None if self.cleared else (self.start.value(), self.end.value())


class AiImportDialog(QDialog):
    PAGE_PICK, PAGE_REVIEW = 0, 1

    def __init__(
        self, *, subjects: list[str], today: date,
        extract: Callable[[list[ImageData]], list[ExtractedDeadline]],
        is_duplicate: Callable[[str, object], bool] = lambda title, due: False,
        runner: Callable = thread_runner, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{tr('aiimport.window')} – {APP_NAME}")
        self.setAcceptDrops(True)
        self.resize(1240, 560)
        self._subjects = list(subjects)
        self._today = today
        self._extract = extract
        self._is_duplicate = is_duplicate
        self._runner = runner
        self._images: list[tuple[str, ImageData, QPixmap]] = []
        self._rows: list[dict] = []
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_pick_page())
        self.stack.addWidget(self._build_review_page())

        self.error_label = QLabel(objectName="ErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        QShortcut(QKeySequence.StandardKey.Paste, self).activated.connect(self.paste_image)

    # ------------------------------------------------------------------ Trang 1: chọn ảnh
    def _build_pick_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(QLabel(tr("aiimport.window"), objectName="DetailTitle"))
        intro = QLabel(tr("aiimport.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.image_list = QListWidget()
        self.image_list.setViewMode(QListView.ViewMode.IconMode)
        self.image_list.setIconSize(THUMB)
        self.image_list.setGridSize(QSize(THUMB.width() + 24, THUMB.height() + 28))
        self.image_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.image_list.setMovement(QListView.Movement.Static)
        self.image_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.image_list.itemSelectionChanged.connect(self._sync_pick_buttons)
        layout.addWidget(self.image_list, 1)

        row = QHBoxLayout()
        self.add_button = QPushButton(tr("aiimport.add_images"))
        self.add_button.clicked.connect(self.add_images_from_dialog)
        self.paste_button = QPushButton(tr("aiimport.paste"))
        self.paste_button.setToolTip("Ctrl+V")
        self.paste_button.clicked.connect(self.paste_image)
        self.remove_button = QPushButton(tr("aiimport.remove"))
        self.remove_button.clicked.connect(self.remove_selected)
        self.key_button = QPushButton(tr("aiimport.key_button"))
        self.key_button.clicked.connect(self.change_key)
        for button in (self.add_button, self.paste_button, self.remove_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.key_button)
        layout.addLayout(row)

        privacy = QLabel(tr("aiimport.privacy"), objectName="SettingsHint")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.status_label = QLabel("")
        buttons.addWidget(self.status_label, 1)
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        self.analyze_button = QPushButton(tr("aiimport.analyze"), objectName="PrimaryButton")
        self.analyze_button.setDefault(True)
        self.analyze_button.clicked.connect(self.analyze)
        buttons.addWidget(cancel)
        buttons.addWidget(self.analyze_button)
        layout.addLayout(buttons)
        self._sync_pick_buttons()
        return page

    def _sync_pick_buttons(self) -> None:
        idle = not self._busy
        self.analyze_button.setEnabled(idle and bool(self._images))
        self.remove_button.setEnabled(idle and bool(self.image_list.selectedItems()))
        for button in (self.add_button, self.paste_button, self.key_button):
            button.setEnabled(idle)

    # --- thêm / bớt ảnh
    def image_count(self) -> int:
        return len(self._images)

    def add_image(self, image: QImage, name: str) -> bool:
        if len(self._images) >= MAX_IMAGES:
            self._show_error(tr("aiimport.too_many", count=MAX_IMAGES))
            return False
        if image.isNull():
            self._show_error(tr("aiimport.unreadable", name=name))
            return False
        data = encode_image(image)
        thumb = QPixmap.fromImage(image).scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
        self._images.append((name, data, thumb))
        item = QListWidgetItem(QIcon(thumb), name)
        item.setSizeHint(self.image_list.gridSize())
        self.image_list.addItem(item)
        self.error_label.hide()
        self._sync_pick_buttons()
        return True

    def add_image_paths(self, files: list[str]) -> None:
        for path in files:
            if len(self._images) >= MAX_IMAGES:
                self._show_error(tr("aiimport.too_many", count=MAX_IMAGES))
                break
            self.add_image(QImage(path), path.replace("\\", "/").rsplit("/", 1)[-1])

    def add_images_from_dialog(self) -> None:
        self.add_image_paths(prompts.pick_image_files(self))

    def paste_image(self) -> None:
        if self.stack.currentIndex() != self.PAGE_PICK or self._busy:
            return
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self._show_error(tr("aiimport.no_clipboard"))
            return
        self.add_image(image, f"{tr('aiimport.pasted')} {len(self._images) + 1}")

    def remove_selected(self) -> None:
        rows = sorted((self.image_list.row(i) for i in self.image_list.selectedItems()), reverse=True)
        for row in rows:
            self.image_list.takeItem(row)
            del self._images[row]
        self._sync_pick_buttons()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if self.stack.currentIndex() != self.PAGE_PICK or self._busy:
            return
        mime = event.mimeData()
        if mime.hasUrls():
            self.add_image_paths([u.toLocalFile() for u in mime.urls() if u.isLocalFile()])
        elif mime.hasImage():
            self.add_image(QImage(mime.imageData()), tr("aiimport.pasted"))
        event.acceptProposedAction()

    def change_key(self) -> None:
        prompts.edit_api_key(self)

    # --- phân tích
    def analyze(self) -> None:
        if self._busy or not self._images:
            return
        self._busy = True
        self.error_label.hide()
        self.progress.show()
        self.status_label.setText(tr("aiimport.analyzing"))
        self._sync_pick_buttons()
        images = [data for _, data, _ in self._images]
        self._runner(lambda: self._extract(images), self._on_extracted, self._on_failed)

    def _finish_busy(self) -> None:
        self._busy = False
        self.progress.hide()
        self.status_label.setText("")
        self._sync_pick_buttons()

    def _on_failed(self, error: AiImportError) -> None:
        self._finish_busy()
        kind = error.kind if error.kind in AiImportError.KINDS else "server"
        message = tr(f"aiimport.err_{kind}")
        if error.detail and kind in ("server", "parse"):
            message += f"\n({error.detail})"
        self._show_error(message)
        if kind in ("no_key", "auth"):
            self.key_button.setFocus()

    def _on_extracted(self, found: list[ExtractedDeadline]) -> None:
        self._finish_busy()
        if not found:
            self._show_error(tr("aiimport.none_found"))
            return
        self._fill_review(found)
        self.stack.setCurrentIndex(self.PAGE_REVIEW)

    # ------------------------------------------------------------------ Trang 2: xem trước
    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(QLabel(tr("aiimport.review_title"), objectName="DetailTitle"))
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", tr("aiimport.col_subject"), tr("aiimport.col_title"),
                                              tr("aiimport.col_due"), tr("aiimport.col_remind"),
                                              tr("aiimport.col_study"), ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column, mode, width in (
            (0, QHeaderView.ResizeMode.Fixed, 40), (1, QHeaderView.ResizeMode.Interactive, 160),
            (2, QHeaderView.ResizeMode.Stretch, 0), (3, QHeaderView.ResizeMode.Fixed, 236),
            (4, QHeaderView.ResizeMode.Fixed, 216), (5, QHeaderView.ResizeMode.Fixed, 150),
            (6, QHeaderView.ResizeMode.Fixed, 110),
        ):
            header.setSectionResizeMode(column, mode)
            if width:
                self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        back = QPushButton(tr("aiimport.back"))
        back.clicked.connect(lambda: self.stack.setCurrentIndex(self.PAGE_PICK))
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("", objectName="PrimaryButton")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(back)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        return page

    def _fill_review(self, found: list[ExtractedDeadline]) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        known = list(self._subjects)
        for item in sorted(found, key=lambda d: d.due_at):
            row = self.table.rowCount()
            self.table.insertRow(row)
            duplicate = self._is_duplicate(item.title, item.due_at)

            check = QCheckBox()
            check.setChecked(not duplicate)
            check.toggled.connect(self._sync_save)
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(8, 0, 4, 0)
            holder_layout.addWidget(check)
            self.table.setCellWidget(row, 0, holder)

            subject = QComboBox()
            subject.setEditable(True)
            subject.addItems(known)
            subject.setCurrentText(match_subject(item.course, known))
            self.table.setCellWidget(row, 1, subject)

            title = QLineEdit(item.title)
            title.textChanged.connect(self._sync_save)
            self.table.setCellWidget(row, 2, title)

            due = DateTimeRow()
            due.set_value(item.due_at)
            self.table.setCellWidget(row, 3, due)

            remind = ReminderEdit()
            remind.enabled.setText("")  # tiêu đề cột đã nói rõ là "Nhắc trước"
            self.table.setCellWidget(row, 4, remind)

            study_button = QPushButton(tr("aiimport.study_add"))
            study_button.setToolTip(tr("aiimport.study_tip"))
            entry = {"check": check, "subject": subject, "title": title, "due": due, "remind": remind,
                     "study": None, "study_button": study_button, "notes": item.notes}
            study_button.clicked.connect(lambda _=False, e=entry: self._edit_study(e))
            self.table.setCellWidget(row, 5, study_button)

            note = QLabel("")
            note.setContentsMargins(6, 0, 8, 0)
            if duplicate:
                note.setText(tr("aiimport.duplicate"))
                note.setToolTip(tr("aiimport.duplicate_tip"))
            elif item.uncertain:
                note.setText(tr("aiimport.uncertain"))
                note.setToolTip(tr("aiimport.uncertain_tip"))
                note.setStyleSheet("color: #c77700; font-weight: 600;")
            self.table.setCellWidget(row, 6, note)
            self.table.setRowHeight(row, 44)
            self._rows.append(entry)
        uncertain = sum(1 for d in found if d.uncertain)
        text = tr("aiimport.found", count=len(found))
        if uncertain:
            text += " " + tr("aiimport.found_uncertain", count=uncertain)
        self.summary_label.setText(text)
        self._sync_save()

    def _selected_rows(self) -> list[dict]:
        return [r for r in self._rows if r["check"].isChecked() and r["title"].text().strip()]

    def _sync_save(self) -> None:
        count = len(self._selected_rows())
        self.save_button.setText(tr("aiimport.add_n", count=count))
        self.save_button.setEnabled(count > 0)

    # --- Study time (tuỳ chọn, từng dòng)
    def default_study_slot(self, due: datetime) -> tuple[datetime, datetime]:
        """Gợi ý: 19:00 tối hôm trước hạn (không sớm hơn hôm nay), dài 1 giờ."""
        start = datetime.combine(due.date() - timedelta(days=1), time(19, 0))
        if start.date() < self._today:
            start = datetime.combine(self._today, time(19, 0))
        return start, start + timedelta(hours=1)

    def set_study(self, entry: dict, slot: tuple[datetime, datetime] | None) -> None:
        entry["study"] = slot
        button: QPushButton = entry["study_button"]
        if slot is None:
            button.setText(tr("aiimport.study_add"))
            button.setProperty("active", False)
        else:
            button.setText(f"📖 {slot[0]:%d/%m %H:%M}")
            button.setProperty("active", True)
        button.style().unpolish(button)
        button.style().polish(button)

    def _edit_study(self, entry: dict) -> None:
        current = entry["study"] or self.default_study_slot(entry["due"].value())
        dialog = StudySlotDialog(entry["title"].text().strip(), *current, has_value=entry["study"] is not None,
                                 parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_study(entry, dialog.value())

    # ------------------------------------------------------------------ Kết quả
    def entries(self) -> list[ImportEntry]:
        return [
            ImportEntry(
                Deadline(title=r["title"].text().strip(), due_at=r["due"].value(),
                         subject=r["subject"].currentText().strip(), notes=r["notes"],
                         remind_before_minutes=r["remind"].value()),
                r["study"],
            )
            for r in self._selected_rows()
        ]

    def deadlines(self) -> list[Deadline]:
        return [entry.deadline for entry in self.entries()]

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()



_LINK_RE = re.compile(r"(?<![\w/.])((?:aistudio\.google\.com|console\.anthropic\.com)(?:[\w/\-]|\.(?=[\w/\-]))*)")


def linkify(text: str) -> str:
    """Văn bản thuần → HTML, tên miền aistudio.google.com / console.anthropic.com thành link bấm được."""
    escaped = html.escape(text).replace("\n", "<br>")
    return _LINK_RE.sub(lambda m: f'<a href="https://{m.group(1)}">{m.group(1)}</a>', escaped)


class ApiKeyDialog(QDialog):
    """Nhập khóa API. Link tạo khóa bấm là mở trình duyệt mặc định (không phải gõ lại bằng tay)."""

    def __init__(self, current: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{tr('aiimport.key_title')} – {APP_NAME}")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        text = QLabel(linkify(tr("aiimport.key_text")))
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)  # bấm link ⇒ trình duyệt mặc định
        layout.addWidget(text)

        self.open_button = QPushButton(tr("aiimport.key_open"))
        self.open_button.clicked.connect(self.open_key_page)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.key_edit = QLineEdit(current)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("AIza…")
        layout.addWidget(self.key_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.key_edit.setFocus()

    def open_key_page(self) -> None:
        QDesktopServices.openUrl(QUrl(GEMINI_KEY_URL))

    def key(self) -> str:
        return self.key_edit.text().strip()
