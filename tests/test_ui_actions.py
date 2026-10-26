"""Thao tác trên cửa sổ chính: thêm/sửa/xoá, mở file, file bị mất, menu chuột phải."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from app import paths
from app.files import FileService
from app.models import Attachment, Event, Deadline, DeadlineStatus
from app.settings import Settings
from app.ui.main_window import MainWindow

pytestmark = pytest.mark.usefixtures("qapp")
NOW = datetime(2026, 10, 13, 16, 0)
TOMORROW = date(2026, 10, 14)


@pytest.fixture
def docx(tmp_path) -> Path:
    path = tmp_path / "BaiTap" / "Lab2.docx"
    path.parent.mkdir()
    path.write_text("x")
    return path


@pytest.fixture
def window(repo, launcher, prompts_stub):
    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: NOW)
    yield win
    win.close()
    win.deleteLater()


def fill_and_accept(fill):
    """Thay dialog.exec(): điền form bằng `fill` rồi giả lập bấm Lưu."""
    def run(dialog):
        fill(dialog)
        dialog.accept()
        return dialog.result() == 1
    return run


# ------------------------------------------------------------------ Luồng chính
def test_create_task_with_docx_then_open_from_detail(window, repo, launcher, docx):
    def fill(dlg):
        dlg.title_edit.setText("Lab 2")
        dlg.due.set_value(datetime(2026, 10, 14, 23, 59))
        dlg.attachments.add_targets([str(docx)])

    window._exec_dialog = fill_and_accept(fill)
    window.new_deadline()
    task = repo.search("Lab 2")[0]
    assert window.selected_day == TOMORROW and window.panel.detail_item_id() == task.id

    window.on_calendar_item(task.id, TOMORROW)        # bấm chip: chỉ hiện chi tiết
    assert launcher.opened == [] and window.panel.detail_item_id() == task.id
    window.panel.detail.attachment_rows[0].open_button.click()   # bấm tên file trong chi tiết
    assert launcher.opened == [str(docx)]


# ------------------------------------------------------------------ File bị mất
@pytest.fixture
def missing_task(repo, tmp_path):
    return repo.add_deadline(Deadline("Mất file", datetime(2026, 10, 14, 9),
                              attachments=[Attachment(str(tmp_path / "da_chuyen.docx"))]))


def open_first_attachment(window, task):
    window.open_attachment_by_id(task.attachments[0].id)


def test_missing_file_relink_then_opens(window, repo, launcher, prompts_stub, missing_task, docx):
    prompts_stub.missing_action = "relink"
    prompts_stub.replacement = str(docx)
    window.open_item(missing_task.id)
    open_first_attachment(window, missing_task)
    assert repo.get_deadline(missing_task.id).attachments[0].target == str(docx)
    assert launcher.opened == [str(docx)]
    assert window.panel.detail_item_id() == missing_task.id


def test_missing_file_remove(window, repo, prompts_stub, missing_task):
    prompts_stub.missing_action = "remove"
    open_first_attachment(window, missing_task)
    assert repo.get_deadline(missing_task.id).attachments == []


def test_missing_file_cancel_does_nothing(window, repo, launcher, prompts_stub, missing_task):
    open_first_attachment(window, missing_task)
    assert launcher.opened == [] and len(repo.get_deadline(missing_task.id).attachments) == 1
    assert prompts_stub.errors == []


def test_open_failure_shows_message(window, repo, launcher, prompts_stub, docx):
    launcher.fail = OSError("No application is associated with the specified file")
    task = repo.add_deadline(Deadline("A", datetime(2026, 10, 14, 9), attachments=[Attachment(str(docx))]))
    window.open_attachment_by_id(task.attachments[0].id)
    assert prompts_stub.errors and "không mở được" in prompts_stub.errors[0]


def test_reveal_containing_folder(window, repo, launcher, docx):
    task = repo.add_deadline(Deadline("A", datetime(2026, 10, 14, 9), attachments=[Attachment(str(docx))]))
    window.reveal_attachment_by_id(task.attachments[0].id)
    assert launcher.revealed == [(str(docx), True)]


def test_detail_attachment_rows(window, repo, docx, tmp_path):
    task = repo.add_deadline(Deadline("A", datetime(2026, 10, 14, 9),
                              attachments=[Attachment(str(docx)), Attachment(str(tmp_path / "mat.pdf"))]))
    window.open_item(task.id)
    rows = window.panel.detail.attachment_rows
    assert [r.missing for r in rows] == [False, True]
    rows[0].open_button.click()


# ------------------------------------------------------------------ Sửa / xoá / trạng thái
def test_edit_event(window, repo):
    event = repo.add_event(Event("Họp", datetime(2026, 10, 15, 9), datetime(2026, 10, 15, 10)))
    window._exec_dialog = fill_and_accept(lambda dlg: dlg.location.setText("Phòng B2"))
    window.edit_item(event.id)
    assert repo.get_event(event.id).location == "Phòng B2"
    assert window.panel.detail.field_values()["Địa điểm"] == "Phòng B2"


def test_cancel_dialog_saves_nothing(window, repo):
    window._exec_dialog = lambda dlg: False
    window.new_deadline()
    window.new_event()
    assert repo.count_items() == {"deadline": 0, "event": 0, "study": 0, "class": 0}


def test_delete_asks_for_confirmation(window, repo, prompts_stub):
    task = repo.add_deadline(Deadline("Xoá tôi", datetime(2026, 10, 14, 9)))
    prompts_stub.confirm_answer = False
    window.delete_item(task.id)
    assert repo.get_deadline(task.id) is not None
    prompts_stub.confirm_answer = True
    window.delete_item(task.id)
    assert repo.get_deadline(task.id) is None and "Xoá tôi" in prompts_stub.confirms[-1]


def test_status_change_from_detail_keeps_detail_open(window, repo):
    task = repo.add_deadline(Deadline("A", datetime(2026, 10, 14, 9)))
    window.open_item(task.id)
    box = window.panel.detail.status_box
    box.setCurrentIndex(box.findData(DeadlineStatus.DONE))
    assert repo.get_deadline(task.id).is_done
    assert window.panel.detail_item_id() == task.id


def test_context_menu_actions(window, repo, docx, launcher):
    task = repo.add_deadline(Deadline("A", datetime(2026, 10, 14, 9), attachments=[Attachment(str(docx))]))
    menu = window.build_item_menu(task.id)
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert list(actions) == ["Xem chi tiết", "Mở “Lab2.docx”", "Sửa…", "Đánh dấu xong ✓", "Xoá"]
    actions["Mở “Lab2.docx”"].trigger()
    assert launcher.opened == [str(docx)]
    actions["Đánh dấu xong ✓"].trigger()
    assert repo.get_deadline(task.id).is_done
    assert "Đánh dấu chưa làm" in [a.text() for a in window.build_item_menu(task.id).actions()]


def test_double_click_day_prefills_due(window, repo):
    seen = {}

    def capture(dlg):
        seen["due"] = dlg.due.value()
        return False

    window._exec_dialog = capture
    window.month_view.cell_for(date(2026, 10, 20)).day_double_clicked.emit(date(2026, 10, 20))
    assert seen["due"] == datetime(2026, 10, 20, 23, 59)


def test_copy_option_copies_into_app_folder(window, repo, docx):
    def fill(dlg):
        dlg.title_edit.setText("Có copy")
        dlg.attachments.add_targets([str(docx)])
        dlg.copy_files.setChecked(True)

    window._exec_dialog = fill_and_accept(fill)
    window.new_deadline()
    stored = Path(repo.search("Có copy")[0].attachments[0].target)
    assert stored.parent == paths.attachments_dir() and stored.read_text() == "x"


def test_copy_existing_attachment_then_delete_original_still_opens(window, repo, launcher, docx):
    """Đúng kịch bản bạn test: file đính kèm từ trước, sau đó mới tích 'Lưu bản sao'."""
    task = repo.add_deadline(Deadline("Cũ", datetime(2026, 10, 14, 9), attachments=[Attachment(str(docx))]))
    window._exec_dialog = fill_and_accept(lambda dlg: dlg.copy_files.setChecked(True))
    window.edit_item(task.id)
    copy_path = Path(repo.get_deadline(task.id).attachments[0].target)
    assert copy_path.parent == paths.attachments_dir()

    docx.unlink()                                             # xoá file gốc trên máy
    window.open_attachment_by_id(repo.get_deadline(task.id).attachments[0].id)
    assert launcher.opened == [str(copy_path)]


def test_copies_cleaned_when_task_deleted_or_attachment_removed(window, repo, docx, prompts_stub):
    def fill(dlg):
        dlg.title_edit.setText("Có copy")
        dlg.attachments.add_targets([str(docx)])
        dlg.copy_files.setChecked(True)

    window._exec_dialog = fill_and_accept(fill)
    window.new_deadline()
    task = repo.search("Có copy")[0]
    copy_path = Path(task.attachments[0].target)

    def remove_all(dlg):
        dlg.attachments.list.setCurrentRow(0)
        dlg.attachments.remove_selected()

    window._exec_dialog = fill_and_accept(remove_all)
    window.edit_item(task.id)                                 # gỡ đính kèm → bản sao bị dọn
    assert not copy_path.exists() and docx.exists()

    window._exec_dialog = fill_and_accept(fill)
    window.new_deadline()
    task2 = repo.search("Có copy")[-1]
    copy2 = Path(task2.attachments[0].target)
    window.delete_item(task2.id)                              # xoá bài tập → bản sao bị dọn
    assert not copy2.exists() and docx.exists()
    assert "bản sao" in prompts_stub.confirms[-1]


def test_subjects_suggested_in_dialog(window, repo):
    repo.add_deadline(Deadline("A", datetime(2026, 10, 14, 9), subject="Phương pháp tính"))
    seen = {}

    def capture(dlg):
        seen["subjects"] = [dlg.subject.itemText(i) for i in range(dlg.subject.count())]
        return False

    window._exec_dialog = capture
    window.new_deadline()
    assert seen["subjects"] == ["Phương pháp tính"]
