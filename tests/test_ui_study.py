"""Deadline ↔ Study time trên giao diện: luồng tạo/liên kết, xoá, panel chi tiết, dialog, phím tắt, hiển thị."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QPushButton, QWidget

from app.files import FileService
from app.models import Deadline, DeadlineStatus, Event, StudyTime
from app.settings import Settings
from app.ui.dialogs import StudyTimeDialog
from app.ui.main_window import MainWindow

pytestmark = pytest.mark.usefixtures("qapp")
NOW = datetime(2026, 10, 13, 16, 0)
DUE = datetime(2026, 10, 20, 23, 59)


@pytest.fixture
def window(repo, launcher, prompts_stub):
    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: NOW)
    yield win
    win.close()
    win.deleteLater()


def accept_with(fill=lambda dlg: None):
    """Thay dialog.exec(): điền form rồi giả lập bấm Lưu; ghi lại dialog đã mở."""
    opened = []

    def run(dialog):
        opened.append(dialog)
        fill(dialog)
        dialog.accept()
        return dialog.result() == 1

    run.opened = opened
    return run


def study(repo, title="Học", deadline_id=None, hours=0):
    start = NOW + timedelta(days=1, hours=hours)
    return repo.add_study_time(StudyTime(title, start, start + timedelta(hours=1), deadline_id=deadline_id))


# ------------------------------------------------------------------ Tạo Deadline mới → hỏi Study time
def create_deadline(window, title="Lab 2"):
    def fill(dlg):
        if isinstance(dlg, StudyTimeDialog):  # hộp thoại thứ hai (sau câu hỏi "Đặt thêm thời gian?"): giữ nguyên điền sẵn
            return
        dlg.title_edit.setText(title)
        dlg.due.set_value(DUE)
        dlg.subject.setCurrentText("Toán")

    window._exec_dialog = accept_with(fill)
    window.new_deadline()


def test_new_deadline_asks_and_yes_opens_prefilled_study_dialog(window, repo, prompts_stub):
    prompts_stub.study_after = True
    created = []

    def fake_exec(dialog):
        if isinstance(dialog, StudyTimeDialog):
            created.append(dialog)
            dialog.accept()
            return True
        dialog.title_edit.setText("Lab 2")
        dialog.due.set_value(DUE)
        dialog.subject.setCurrentText("Toán")
        dialog.accept()
        return True

    window._exec_dialog = fake_exec
    window.new_deadline()
    deadline = repo.search("Lab 2", kind="deadline")[0]
    assert prompts_stub.study_after_asked == 1
    (dialog,) = created
    assert dialog.title_edit.text() == "Lab 2"                       # sao chép tiêu đề
    assert dialog.deadline.currentData() == deadline.id              # liên kết sẵn
    assert dialog.subject.currentText() == "Toán"                    # môn mặc định theo deadline
    (saved,) = repo.study_times_for(deadline.id)
    assert saved.title == "Lab 2" and saved.subject == "Toán"
    assert window.panel.detail_item_id() == saved.id                 # mở chi tiết của Study time vừa tạo


def test_new_deadline_answer_no_just_closes(window, repo, prompts_stub):
    prompts_stub.study_after = False
    create_deadline(window)
    assert prompts_stub.study_after_asked == 1
    assert repo.count_items() == {"deadline": 1, "event": 0, "study": 0}
    assert window.panel.detail_item_id() == repo.search("Lab 2")[0].id


def test_editing_deadline_does_not_ask(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Cũ", DUE))
    window._exec_dialog = accept_with(lambda dlg: dlg.title_edit.setText("Mới"))
    window.edit_item(deadline.id)
    assert prompts_stub.study_after_asked == 0 and repo.get_deadline(deadline.id).title == "Mới"


def test_title_is_copied_once_not_synced(window, repo, prompts_stub):
    prompts_stub.study_after = True
    create_deadline(window, "Lab 2")
    deadline = repo.search("Lab 2", kind="deadline")[0]
    deadline.title = "Lab 2 (đã đổi tên)"
    repo.update_deadline(deadline)
    assert repo.study_times_for(deadline.id)[0].title == "Lab 2"


# ------------------------------------------------------------------ Study time dialog
def test_study_dialog_lists_only_open_deadlines_default_empty(repo):
    open_dl = repo.add_deadline(Deadline("Mở", DUE))
    repo.add_deadline(Deadline("Xong", DUE, status=DeadlineStatus.DONE))
    dlg = StudyTimeDialog(subjects=[], deadlines=repo.open_deadlines())
    assert dlg.deadline.count() == 2 and dlg.deadline.currentData() is None   # trống + 1 deadline chưa xong
    assert dlg.deadline.itemData(1) == open_dl.id


def test_study_dialog_subject_follows_deadline_until_user_types(repo):
    a = repo.add_deadline(Deadline("A", DUE, subject="Toán"))
    b = repo.add_deadline(Deadline("B", DUE, subject="Lý"))
    dlg = StudyTimeDialog(subjects=["Toán", "Lý"], deadlines=repo.open_deadlines())
    dlg.deadline.setCurrentIndex(dlg.deadline.findData(a.id))
    assert dlg.subject.currentText() == "Toán"
    dlg.deadline.setCurrentIndex(dlg.deadline.findData(b.id))
    assert dlg.subject.currentText() == "Lý"                          # còn là môn tự điền → đổi theo
    dlg.subject.setCurrentText("Tự gõ")
    dlg.deadline.setCurrentIndex(dlg.deadline.findData(a.id))
    assert dlg.subject.currentText() == "Tự gõ"                       # người dùng đã gõ → giữ nguyên


def test_study_dialog_validates_end_after_start_and_keeps_duration(repo):
    dlg = StudyTimeDialog(subjects=[], deadlines=[], default_start=datetime(2026, 10, 14, 9))
    dlg.title_edit.setText("X")
    assert dlg.end.value() - dlg.start.value() == timedelta(hours=1)
    dlg.start.set_value(datetime(2026, 10, 14, 14))
    assert dlg.end.value() == datetime(2026, 10, 14, 15)              # đổi giờ bắt đầu → kết thúc dời theo
    dlg.end.set_value(dlg.start.value())                              # kết thúc = bắt đầu → không hợp lệ
    dlg.accept()
    assert dlg.result() == 0 and not dlg.error_label.isHidden()


def test_editing_study_linked_to_done_deadline_keeps_link(window, repo):
    deadline = repo.add_deadline(Deadline("Đã xong", DUE, status=DeadlineStatus.DONE))
    s = study(repo, deadline_id=deadline.id)
    window._exec_dialog = accept_with(lambda dlg: dlg.title_edit.setText("Sửa"))
    window.edit_item(s.id)
    assert repo.get_study_time(s.id).title == "Sửa" and repo.get_study_time(s.id).deadline_id == deadline.id


# ------------------------------------------------------------------ Xoá
def test_delete_deadline_with_study_times_ask_delete(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    s = study(repo, deadline_id=deadline.id)
    prompts_stub.links_action = "delete"
    window.delete_item(deadline.id)
    assert prompts_stub.links_asked == [("Lab", 1)]
    assert repo.get_deadline(deadline.id) is None and repo.get_study_time(s.id) is None
    assert prompts_stub.confirms == []                                # không hiện thêm hộp xác nhận thường


def test_delete_deadline_with_study_times_ask_unlink(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    s = study(repo, deadline_id=deadline.id)
    prompts_stub.links_action = "unlink"
    window.delete_item(deadline.id)
    assert repo.get_deadline(deadline.id) is None and repo.get_study_time(s.id).deadline_id is None


def test_delete_deadline_with_study_times_cancel_changes_nothing(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    study(repo, deadline_id=deadline.id)
    prompts_stub.links_action = "cancel"
    window.delete_item(deadline.id)
    assert repo.get_deadline(deadline.id) is not None and repo.count_items()["study"] == 1


def test_delete_deadline_without_study_times_uses_plain_confirm(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Trống", DUE))
    window.delete_item(deadline.id)
    assert prompts_stub.links_asked == [] and len(prompts_stub.confirms) == 1
    assert repo.get_deadline(deadline.id) is None


def test_delete_study_time_never_asks_about_deadline(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    s = study(repo, deadline_id=deadline.id)
    window.delete_item(s.id)
    assert prompts_stub.links_asked == [] and "Study time" in prompts_stub.confirms[0]
    assert repo.get_study_time(s.id) is None and repo.get_deadline(deadline.id) is not None


# ------------------------------------------------------------------ Panel chi tiết
def test_deadline_detail_lists_linked_study_times_clickable(window, repo):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    s1, s2 = study(repo, "Buổi 1", deadline.id), study(repo, "Buổi 2", deadline.id, hours=24)
    window.open_item(deadline.id)
    detail = window.panel.detail
    assert not detail._study_box.isHidden()
    assert len(detail.study_rows) == 2 and detail.add_study_button is None
    detail.study_rows[1].click()                                        # bấm → mở chi tiết Study time
    assert window.panel.detail_item_id() == s2.id


def test_deadline_without_study_shows_add_button_prefilled(window, repo, prompts_stub):
    deadline = repo.add_deadline(Deadline("Lab 2", DUE, subject="Toán"))
    window.open_item(deadline.id)
    detail = window.panel.detail
    assert detail.study_rows == [] and detail.add_study_button is not None
    window._exec_dialog = accept_with()
    detail.add_study_button.click()
    (dialog,) = window._exec_dialog.opened
    assert dialog.title_edit.text() == "Lab 2" and dialog.deadline.currentData() == deadline.id
    assert len(repo.study_times_for(deadline.id)) == 1


def test_study_detail_has_linked_card_and_countdown_slot(window, repo):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    s = study(repo, "Buổi 1", deadline.id)
    window.open_item(s.id)
    detail = window.panel.detail
    assert detail._study_box.isHidden()                                 # không có khối Study time ở trang Study time
    assert detail.linked_card is not None
    slot = detail.findChild(QWidget, "countdown_slot")
    assert slot is not None and slot is detail.countdown_slot
    assert [w.objectName() for w in slot.findChildren(QWidget, "CountdownTimer")] == ["CountdownTimer"]  # chỉ có đồng hồ nhỏ
    assert detail._extras.indexOf(detail.linked_card) < detail._extras.indexOf(slot)   # thẻ deadline nằm trên slot
    detail.linked_card.clicked.emit()                                   # thẻ deadline bấm được
    assert window.panel.detail_item_id() == deadline.id


def test_study_without_deadline_has_no_card_but_still_has_slot(window, repo):
    s = study(repo, "Học Tiếng Anh")
    window.open_item(s.id)
    assert window.panel.detail.linked_card is None
    assert window.panel.detail.findChild(QWidget, "countdown_slot") is not None


def test_countdown_slot_never_exists_for_deadline_or_event(window, repo):
    deadline = repo.add_deadline(Deadline("Lab", DUE))
    event = repo.add_event(Event("Họp", NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1)))
    s = study(repo, deadline_id=deadline.id)
    window.open_item(s.id)                                              # slot tồn tại ở Study time…
    assert window.panel.detail.findChild(QWidget, "countdown_slot") is not None
    for item_id in (deadline.id, event.id):                             # …nhưng biến mất ở Deadline/Event
        window.open_item(item_id)
        window.panel.detail._clear_extras  # noqa: B018 – chỉ để chắc phương thức tồn tại
        assert window.panel.detail.countdown_slot is None
        assert all(w.objectName() != "countdown_slot" for w in window.panel.detail.findChildren(QWidget)
                   if w.isVisible() or not w.isHidden())


# ------------------------------------------------------------------ Toolbar & phím tắt
def test_toolbar_has_three_buttons_in_order(window):
    bar = window.findChild(QWidget, "Toolbar")
    texts = [b.text() for b in bar.findChildren(QPushButton) if b.text().startswith("+")]
    assert texts == ["+ Deadline", "+ Study time", "+ Sự kiện"]


def test_shortcuts_ctrl_n_and_ctrl_alt_n(window, monkeypatch):
    calls = []
    monkeypatch.setattr(MainWindow, "new_deadline", lambda self, day=None: calls.append("deadline"))
    monkeypatch.setattr(MainWindow, "new_study_time", lambda self, *a, **k: calls.append("study"))
    monkeypatch.setattr(MainWindow, "new_event", lambda self, day=None: calls.append("event"))
    window._build_shortcuts()
    shortcuts = {sc.key().toString(): sc for sc in window.findChildren(QShortcut)}
    for keys in ("Ctrl+N", "Ctrl+Alt+N", "Ctrl+Shift+N"):
        assert keys in shortcuts or QKeySequence(keys).toString() in shortcuts
        shortcuts.get(keys, shortcuts.get(QKeySequence(keys).toString())).activated.emit()
    assert calls[-3:] == ["deadline", "study", "event"]


# ------------------------------------------------------------------ Hiển thị trong lịch & danh sách
def test_deadline_chip_and_card_show_icon_count_and_warning(window, repo):
    soon = repo.add_deadline(Deadline("Gấp", NOW + timedelta(hours=5)))          # <24h
    far = repo.add_deadline(Deadline("Xa", NOW + timedelta(days=5)))
    study(repo, "S1", far.id)
    study(repo, "S2", far.id)
    window.refresh()
    cell = window.month_view.cell_for(soon.due_at.date())
    chip = next(c for c in cell._chips if not c.isHidden() and "Gấp" in c.fullText())
    assert chip.fullText().startswith("⏳") and chip.fullText().endswith("📖0")
    assert chip.property("state") == "soon" and chip.property("zero") is True

    far_cell = window.month_view.cell_for(far.due_at.date())
    far_chip = next(c for c in far_cell._chips if not c.isHidden() and "Xa" in c.fullText())
    assert far_chip.fullText().endswith("📖2") and far_chip.property("zero") is False

    window.on_day_clicked(soon.due_at.date())
    card = next(c for c in window.panel.list._cards if c.item_id == soon.id)
    assert card.study_badge.text() == "📖 0" and card.study_badge.property("zero") is True
    assert "left" in card.study_badge.toolTip() or "Chưa đặt" in card.study_badge.toolTip()
    window.on_day_clicked(far.due_at.date())
    far_card = next(c for c in window.panel.list._cards if c.item_id == far.id)
    assert far_card.study_badge.text() == "📖 2" and far_card.study_badge.property("zero") is False


def test_study_time_renders_like_event_time_block(window, repo):
    s = study(repo, "Học Tiếng Anh")
    window.on_day_clicked(s.starts_at.date())
    card = next(c for c in window.panel.list._cards if c.item_id == s.id)
    assert card.icon.text() == "📖" and card.study_badge is None
    meta = card.findChild(QWidget, "CardMeta")
    assert meta.fullText().startswith(f"{s.starts_at:%H:%M} – {s.ends_at:%H:%M}")


def test_study_time_in_search_and_links_and_cli(window, repo, tmp_path, monkeypatch, capsys):
    s = study(repo, "Ôn Giải tích")
    window.search("giai tich")
    assert window.panel.list.titles() == ["Ôn Giải tích"]
    from app import links
    assert links.parse(links.item_url(s.id)) == ("item", s.id)
    window.focus_item(s.id)
    assert window.panel.detail_item_id() == s.id

    from app import cli, paths
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "cli"))
    assert cli.main(["--seed"]) == 0
    out = capsys.readouterr().out
    assert "study time" in out and "📖" in out and "⏳" in out


def test_seed_data_has_all_three_types_and_link(repo):
    from app.demo_data import seed_demo

    assert seed_demo(repo, NOW) == 9
    counts = repo.count_items()
    assert counts == {"deadline": 4, "study": 3, "event": 2}
    linked = [d for d in repo.open_deadlines() if d.study_count]
    assert len(linked) == 1 and linked[0].study_count == 2
