"""Dialog thêm/sửa Task/Event (chạy offscreen)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from app.files import FileService
from app.models import Attachment, AttachmentKind, Event, Priority, Task, TaskStatus
from app.ui.dialogs import EventDialog, TaskDialog

pytestmark = pytest.mark.usefixtures("qapp", "prompts_stub")
DUE = datetime(2026, 10, 15, 23, 59)


@pytest.fixture
def files_dir(tmp_path):
    folder = tmp_path / "BaiTap"
    folder.mkdir()
    for name in ("lab2.docx", "de.pdf"):
        (folder / name).write_text("x")
    return folder


def new_dialog(launcher, **kwargs) -> TaskDialog:
    return TaskDialog(files=FileService(launcher), subjects=["Toán", "Lý"], default_due=DUE, **kwargs)


def test_new_task_defaults(launcher):
    dlg = new_dialog(launcher)
    assert dlg.due.value() == DUE
    assert dlg.priority.currentData() == Priority.NORMAL
    assert dlg.status.currentData() == TaskStatus.TODO
    assert not dlg.save_button.isEnabled()  # chưa có tiêu đề
    dlg.title_edit.setText("Lab 2")
    assert dlg.save_button.isEnabled()


def test_result_task_collects_fields(launcher, files_dir):
    dlg = new_dialog(launcher)
    dlg.title_edit.setText("  Lab 2  ")
    dlg.subject.setCurrentText("Phương pháp tính")
    dlg.priority.setCurrentIndex(dlg.priority.findData(Priority.HIGH))
    dlg.reminder.set_value(120)
    dlg.notes.setPlainText("Bài 2.1 → 2.5")
    dlg.attachments.add_targets([str(files_dir / "lab2.docx"), "www.lms.edu/lab2"])
    task = dlg.result_item()
    assert task.title == "Lab 2" and task.subject == "Phương pháp tính"
    assert task.priority is Priority.HIGH and task.remind_before_minutes == 120
    assert [a.kind for a in task.attachments] == [AttachmentKind.FILE, AttachmentKind.URL]
    assert not hasattr(dlg, "open_on_click")  # tính năng 'bấm là mở file' đã bỏ


def test_add_targets_skips_duplicates_and_empty(launcher, files_dir):
    dlg = new_dialog(launcher)
    doc = str(files_dir / "lab2.docx")
    assert dlg.attachments.add_targets([doc, f'"{doc}"', "", "  "]) == 1
    assert dlg.attachments.list.count() == 1


def test_attachment_buttons_use_prompts(launcher, files_dir, prompts_stub):
    dlg = new_dialog(launcher)
    prompts_stub.files = [str(files_dir / "lab2.docx"), str(files_dir / "de.pdf")]
    prompts_stub.folder = str(files_dir)
    prompts_stub.link = "https://lms.edu/x"
    dlg.attachments._add_files()
    dlg.attachments._add_folder()
    dlg.attachments._add_link()
    assert [a.kind for a in dlg.attachments.attachments()] == [
        AttachmentKind.FILE, AttachmentKind.FILE, AttachmentKind.FOLDER, AttachmentKind.URL]
    dlg.attachments.list.setCurrentRow(0)
    dlg.attachments.open_selected()
    assert launcher.opened == [str(files_dir / "lab2.docx")]
    dlg.attachments.remove_selected()
    assert dlg.attachments.list.count() == 3


def test_relink_missing_in_dialog(launcher, files_dir, tmp_path, prompts_stub):
    task = Task("A", DUE, id=5, attachments=[Attachment(str(tmp_path / "cu.docx"), id=11)])
    dlg = new_dialog(launcher, task=task)
    assert "không tìm thấy" in dlg.attachments.list.item(0).text()
    prompts_stub.missing_action = "relink"
    prompts_stub.replacement = str(files_dir / "lab2.docx")
    dlg.attachments.list.setCurrentRow(0)
    dlg.attachments.open_selected()
    result = dlg.result_item()
    assert result.id == 5 and result.attachments[0].id == 11  # giữ id → repository sửa tại chỗ
    assert result.attachments[0].target == str(files_dir / "lab2.docx")
    assert "không tìm thấy" not in dlg.attachments.list.item(0).text()


def test_drop_files_and_links(launcher, files_dir):
    dlg = new_dialog(launcher)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(files_dir / "lab2.docx")), QUrl("https://lms.edu/de")])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dlg.dropEvent(event)
    targets = [a.target for a in dlg.attachments.attachments()]
    assert targets[0].endswith("lab2.docx") and targets[1] == "https://lms.edu/de"


def test_edit_task_loads_values(launcher, files_dir):
    task = Task("Lab", DUE, status=TaskStatus.IN_PROGRESS, priority=Priority.LOW, subject="Toán",
                remind_before_minutes=1440, id=3, attachments=[Attachment(str(files_dir / "lab2.docx"), id=9)])
    dlg = new_dialog(launcher, task=task)
    assert dlg.reminder.amount.value() == 1 and dlg.reminder.unit.currentData() == 1440
    assert not dlg.copy_files.isChecked()
    assert dlg.result_item() == Task("Lab", DUE, status=TaskStatus.IN_PROGRESS, priority=Priority.LOW,
                                     subject="Toán", remind_before_minutes=1440, id=3,
                                     attachments=[Attachment(str(files_dir / "lab2.docx"), id=9)])


def test_edit_task_with_existing_copy_keeps_copy_option_on(launcher):
    from app import paths

    copy = paths.attachments_dir() / "lab.docx"
    copy.write_text("x")
    task = Task("Lab", DUE, id=3, attachments=[Attachment(str(copy), id=9)])
    assert new_dialog(launcher, task=task).copy_files.isChecked()


def test_event_dialog_keeps_duration_when_start_moves():
    dlg = EventDialog(default_start=datetime(2026, 10, 15, 9))
    dlg.title_edit.setText("Họp")
    dlg.end.set_value(datetime(2026, 10, 15, 10, 30))
    dlg.start.set_value(datetime(2026, 10, 15, 14))
    event = dlg.result_item()
    assert event.ends_at - event.starts_at == timedelta(hours=1, minutes=30)


def test_event_dialog_rejects_end_before_start():
    dlg = EventDialog(event=Event("Họp", datetime(2026, 10, 15, 9), datetime(2026, 10, 15, 10), id=4))
    dlg.end.blockSignals(True)
    dlg.end.set_value(datetime(2026, 10, 15, 8))
    dlg.end.blockSignals(False)
    dlg.accept()
    assert dlg.result() == 0 and not dlg.error_label.isHidden()
    assert "kết thúc" in dlg.error_label.text()
