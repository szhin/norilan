"""Tự xoá Deadline: xong + 24h, quá hạn + 24h (tính từ mốc đổi hạn/mở lại), chỉ Deadline, sao lưu trước, migration v6."""
from __future__ import annotations

import sqlite3
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app import db, maintenance
from app.db import connect
from app.maintenance import (
    AUTO_DELETE_DELAY,
    auto_clean,
    count_expired,
    select_expired,
    time_until_delete,
)
from app.models import Attachment, Deadline, DeadlineStatus, Event, Exam, StudyTime
from app.repository import Repository
from app.settings import Settings

NOW = datetime(2026, 10, 14, 10, 0)
H = timedelta(hours=1)
ALL_ON = Settings()


@pytest.fixture
def clock(monkeypatch):
    """Đồng hồ của repository (ghi due_changed_at, completed_at...) do test điều khiển."""
    box = {"t": NOW}
    monkeypatch.setattr("app.repository._now", lambda: box["t"])
    return box


def dl(repo, title="D", due=NOW + 5 * H, **kw):
    return repo.add_deadline(Deadline(title, due, **kw))


def ids(items):
    return sorted(i.title for i in items)


def expired(repo, now=NOW, settings=ALL_ON, **kw):
    return ids(select_expired(repo.list_deadlines(), now, settings, **kw))


def test_delay_is_one_named_constant():
    assert AUTO_DELETE_DELAY == timedelta(hours=24)
    assert Settings().auto_delete_done is True and Settings().auto_delete_overdue is True


# ------------------------------------------------------------------ Quy tắc 1: đã xong
def test_done_deadline_deleted_exactly_after_24h_from_completion(repo, clock):
    d = dl(repo, "xong", due=NOW + 10 * 24 * H)       # hạn còn xa: chỉ "đã xong" mới quyết định
    repo.set_deadline_status(d.id, DeadlineStatus.DONE, at=NOW)
    assert expired(repo, NOW + 24 * H - timedelta(minutes=1)) == []       # 23h59
    assert expired(repo, NOW + 24 * H) == ["xong"]                          # 24h00
    assert expired(repo, NOW + 30 * H) == ["xong"]


def test_done_deadline_uses_completion_time_not_due(repo, clock):
    d = dl(repo, "xong", due=NOW - 30 * 24 * H)      # hạn đã quá lâu, nhưng vừa mới đánh dấu xong
    repo.set_deadline_status(d.id, DeadlineStatus.DONE, at=NOW)
    assert expired(repo, NOW + 23 * H) == []
    assert expired(repo, NOW + 24 * H) == ["xong"]


def test_done_without_completion_time_is_never_guessed(repo):
    d = dl(repo, "x", status=DeadlineStatus.DONE, due=NOW - 100 * 24 * H)
    clean = replace(repo.get_deadline(d.id), completed_at=None)
    assert select_expired([clean], NOW + 999 * H, ALL_ON) == []


# ------------------------------------------------------------------ Quy tắc 2: quá hạn
def test_overdue_not_done_deleted_24h_after_due(repo, clock):
    clock["t"] = NOW - 10 * 24 * H                       # tạo từ lâu
    dl(repo, "quá hạn", due=NOW - 24 * H)                # hạn đúng 24h trước…
    clock["t"] = NOW
    repo._conn.execute("UPDATE items SET due_changed_at = NULL")  # …và chưa từng đổi hạn
    assert expired(repo, NOW - timedelta(seconds=1)) == []         # hạn + 23h59'59"
    assert expired(repo, NOW) == ["quá hạn"]                       # hạn + 24h00


def test_not_yet_due_is_never_deleted(repo, clock):
    dl(repo, "tương lai", due=NOW + 1 * H)
    assert expired(repo, NOW) == []
    assert expired(repo, NOW + 24 * H) == []             # hạn + 23h → còn chưa đủ
    assert expired(repo, NOW + 25 * H) == ["tương lai"]


def test_new_deadline_with_past_due_gets_a_fresh_day(repo, clock):
    dl(repo, "vừa thêm", due=NOW - 5 * 24 * H)           # người dùng thêm việc đã trễ 5 ngày
    assert expired(repo, NOW + 23 * H) == []             # không bị xoá ngay
    assert expired(repo, NOW + 24 * H) == ["vừa thêm"]


def test_reopened_deadline_not_deleted_instantly(repo, clock):
    d = dl(repo, "mở lại", due=NOW - 10 * 24 * H)
    repo.set_deadline_status(d.id, DeadlineStatus.DONE, at=NOW - 3 * 24 * H)
    clock["t"] = NOW
    repo.set_deadline_status(d.id, DeadlineStatus.TODO, at=NOW)          # mở lại bây giờ
    reopened = repo.get_deadline(d.id)
    assert reopened.due_changed_at == NOW and reopened.completed_at is None
    assert expired(repo, NOW + 1 * H) == []
    assert expired(repo, NOW + 24 * H) == ["mở lại"]                     # max(due, mở lại) + 24h


def test_reopen_through_edit_dialog_path_also_counts(repo, clock):
    d = dl(repo, "sửa", due=NOW - 10 * 24 * H, status=DeadlineStatus.DONE)
    clock["t"] = NOW
    edited = repo.get_deadline(d.id)
    edited.status = DeadlineStatus.IN_PROGRESS
    repo.update_deadline(edited)
    assert expired(repo, NOW + 23 * H) == [] and expired(repo, NOW + 24 * H) == ["sửa"]


def test_editing_due_date_restarts_the_clock(repo, clock):
    d = dl(repo, "dời hạn", due=NOW - 30 * H)
    assert expired(repo, NOW + 24 * H) == ["dời hạn"]                    # lẽ ra sắp bị xoá…
    clock["t"] = NOW + 2 * H
    edited = repo.get_deadline(d.id)
    edited.due_at = NOW - 40 * H                                         # …sửa hạn (vẫn trong quá khứ)
    repo.update_deadline(edited)
    assert repo.get_deadline(d.id).due_changed_at == NOW + 2 * H
    assert expired(repo, NOW + 25 * H) == []
    assert expired(repo, NOW + 26 * H) == ["dời hạn"]


def test_editing_other_fields_does_not_restart_the_clock(repo, clock):
    d = dl(repo, "đổi tên", due=NOW - 30 * H)
    created = repo.get_deadline(d.id).due_changed_at
    clock["t"] = NOW + 5 * H
    edited = repo.get_deadline(d.id)
    edited.title, edited.notes = "tên mới", "ghi chú"
    repo.update_deadline(edited)
    assert repo.get_deadline(d.id).due_changed_at == created


# ------------------------------------------------------------------ Công tắc
def test_toggles_are_independent(repo, clock):
    done = dl(repo, "xong")
    repo.set_deadline_status(done.id, DeadlineStatus.DONE, at=NOW - 2 * 24 * H)
    dl(repo, "trễ", due=NOW - 3 * 24 * H)
    clock["t"] = NOW - 3 * 24 * H
    repo._conn.execute("UPDATE items SET due_changed_at = NULL WHERE title = 'trễ'")
    assert expired(repo) == ["trễ", "xong"]
    assert expired(repo, settings=Settings(auto_delete_done=False)) == ["trễ"]
    assert expired(repo, settings=Settings(auto_delete_overdue=False)) == ["xong"]
    assert expired(repo, settings=Settings(auto_delete_done=False, auto_delete_overdue=False)) == []


# ------------------------------------------------------------------ Chỉ Deadline; Study time giữ & gỡ liên kết
def clean(repo, tmp_path, now=NOW, settings=ALL_ON, **kw):
    return auto_clean(repo, settings, now, attachments_dir=tmp_path / "attachments",
                      backups_dir=tmp_path / "backups", data_dir=tmp_path, **kw)


def test_study_time_is_never_selected_and_is_unlinked(repo, clock, tmp_path):
    old = NOW - 100 * 24 * H
    d = dl(repo, "cũ", due=old)
    study = repo.add_study_time(StudyTime("Học", old, old + H, deadline_id=d.id))
    far = NOW + 100 * 24 * H
    assert expired(repo, far) == ["cũ"]                                  # chỉ Deadline
    assert clean(repo, tmp_path, now=far) == 1
    assert repo.get_deadline(d.id) is None
    assert repo.get_study_time(study.id).deadline_id is None             # còn, chỉ mất liên kết


def test_event_deleted_exactly_12h_after_it_ends(repo, clock, tmp_path):
    event = repo.add_event(Event("Họp", NOW - 3 * H, NOW - H))           # kết thúc lúc NOW - 1h
    ends = NOW - H
    assert clean(repo, tmp_path, now=ends + 12 * H - timedelta(minutes=1)) == 0     # 11h59
    assert repo.get_event(event.id) is not None
    assert clean(repo, tmp_path, now=ends + 12 * H) == 1                              # 12h00
    assert repo.get_event(event.id) is None


def test_running_and_future_events_and_exams_are_kept(repo, clock, tmp_path):
    running = repo.add_event(Event("Đang họp", NOW - H, NOW + H))
    future = repo.add_event(Event("Mai họp", NOW + 20 * H, NOW + 21 * H))
    exam = repo.add_exam(Exam("Toán", NOW - 30 * 24 * H))                # ca thi cũ không bị tự xoá
    assert clean(repo, tmp_path, now=NOW) == 0
    assert all(repo.get_item(i.id) is not None for i in (running, future, exam))


def test_event_in_edit_dialog_is_skipped_then_deleted(repo, clock, tmp_path):
    event = repo.add_event(Event("Họp", NOW - 30 * H, NOW - 29 * H))
    assert clean(repo, tmp_path, now=NOW, skip_ids={event.id}) == 0
    assert clean(repo, tmp_path, now=NOW) == 1


def test_skip_deadline_open_in_edit_dialog(repo, clock, tmp_path):
    a, b = dl(repo, "a", due=NOW - 2 * 24 * H), dl(repo, "b", due=NOW - 2 * 24 * H)
    far = NOW + 2 * 24 * H
    assert clean(repo, tmp_path, now=far, skip_ids={a.id}) == 1
    assert repo.get_deadline(a.id) is not None and repo.get_deadline(b.id) is None
    assert clean(repo, tmp_path, now=far) == 1                           # lượt sau (đã đóng dialog) thì xoá


def test_clock_moved_backwards_deletes_nothing_more(repo, clock, tmp_path):
    d = dl(repo, "x", due=NOW - 30 * H)
    clock["t"] = NOW - 30 * H
    repo._conn.execute("UPDATE items SET due_changed_at = NULL")
    assert expired(repo, NOW) == ["x"]
    assert expired(repo, NOW - 10 * 24 * H) == []                        # đồng hồ bị chỉnh lùi: mốc chưa tới
    assert clean(repo, tmp_path, now=NOW - 10 * 24 * H) == 0
    assert repo.get_deadline(d.id) is not None


def test_works_after_app_was_closed_for_days(repo, clock, tmp_path):
    dl(repo, "a", due=NOW + H)
    assert clean(repo, tmp_path, now=NOW + timedelta(days=9)) == 1       # chỉ dựa trên mốc thời gian


# ------------------------------------------------------------------ Sao lưu trước + một transaction + đính kèm
def test_backup_taken_before_delete_once_per_day(repo, clock, tmp_path):
    dl(repo, "a", due=NOW - 3 * 24 * H)
    dl(repo, "b", due=NOW - 3 * 24 * H)
    assert clean(repo, tmp_path, now=NOW + 3 * 24 * H) == 2
    (backup,) = (tmp_path / "backups").glob("truoc-khi-tu-xoa-*.zip")
    with zipfile.ZipFile(backup) as archive:                             # bản sao lưu còn đủ dữ liệu trước khi xoá
        copy = tmp_path / "x.db"
        copy.write_bytes(archive.read("norilan.db"))
    check = sqlite3.connect(copy)
    assert check.execute("SELECT COUNT(*) FROM items WHERE kind = 'deadline'").fetchone()[0] == 2
    check.close()
    # Cùng ngày, có thêm deadline hết hạn: xoá nhưng KHÔNG sao lưu thêm
    dl(repo, "c", due=NOW - 3 * 24 * H)
    assert clean(repo, tmp_path, now=NOW + 3 * 24 * H + H) == 1
    assert len(list((tmp_path / "backups").glob("truoc-khi-tu-xoa-*.zip"))) == 1
    # Ngày hôm sau: sao lưu lại
    dl(repo, "d", due=NOW - 3 * 24 * H)
    assert clean(repo, tmp_path, now=NOW + 4 * 24 * H) == 1
    assert len(list((tmp_path / "backups").glob("truoc-khi-tu-xoa-*.zip"))) == 2


def test_no_backup_when_nothing_to_delete(repo, clock, tmp_path):
    dl(repo, "tương lai")
    assert clean(repo, tmp_path) == 0
    assert not (tmp_path / "backups").exists()


def test_backup_failure_aborts_deletion(repo, clock, tmp_path, monkeypatch):
    d = dl(repo, "a", due=NOW - 3 * 24 * H)

    def boom(*a, **k):
        raise OSError("ổ đĩa đầy")

    monkeypatch.setattr(maintenance, "create_backup", boom)
    assert clean(repo, tmp_path, now=NOW + 3 * 24 * H) == 0
    assert repo.get_deadline(d.id) is not None


def test_all_deleted_in_a_single_transaction(repo, clock, tmp_path, monkeypatch):
    for name in "abc":
        dl(repo, name, due=NOW - 3 * 24 * H)
    real = repo._conn
    calls = []

    class Spy:
        def __getattr__(self, name):
            return getattr(real, name)

        def __enter__(self):
            calls.append("enter")
            return real.__enter__()

        def __exit__(self, *a):
            return real.__exit__(*a)

    monkeypatch.setattr(repo, "_conn", Spy())
    assert repo.delete_deadlines([d.id for d in repo.list_deadlines()]) == 3
    assert calls == ["enter"]                                            # một transaction cho cả 3


def test_attachment_copies_removed_originals_kept(repo, clock, tmp_path):
    copies = tmp_path / "attachments"
    copies.mkdir()
    copy, original = copies / "bai.docx", tmp_path / "goc.docx"
    copy.write_text("x")
    original.write_text("y")
    dl(repo, "A", due=NOW - 3 * 24 * H, attachments=[Attachment(str(copy)), Attachment(str(original))])
    assert clean(repo, tmp_path, now=NOW + 3 * 24 * H) == 1
    assert not copy.exists() and original.exists()
    assert repo.all_attachment_targets() == set()                        # đính kèm trong DB xoá theo


def test_count_expired_matches_what_would_be_deleted(repo, clock):
    dl(repo, "a", due=NOW - 3 * 24 * H)
    clock["t"] = NOW - 3 * 24 * H
    repo._conn.execute("UPDATE items SET due_changed_at = NULL")
    assert count_expired(repo, NOW, ALL_ON) == 1
    assert count_expired(repo, NOW, Settings(auto_delete_overdue=False)) == 0


# ------------------------------------------------------------------ Dòng "Tự xoá sau …"
def test_time_until_delete_visibility(repo, clock):
    done = dl(repo, "xong", due=NOW + 100 * H)
    repo.set_deadline_status(done.id, DeadlineStatus.DONE, at=NOW)
    soon = dl(repo, "sắp hạn", due=NOW + 5 * H)
    late = dl(repo, "đã trễ", due=NOW - 5 * H)
    get = lambda d, s=ALL_ON, now=NOW: time_until_delete(repo.get_deadline(d.id), now, s)  # noqa: E731
    assert get(done) == AUTO_DELETE_DELAY                                # đã xong: luôn hiện
    assert get(soon) is None                                             # chưa quá hạn: ẩn
    assert get(late) == AUTO_DELETE_DELAY                                # mới tạo ⇒ mốc là lúc tạo (NOW)
    clock["t"] = NOW - 10 * H
    old = dl(repo, "trễ lâu", due=NOW - 10 * H)
    repo._conn.execute("UPDATE items SET due_changed_at = NULL WHERE id = ?", (old.id,))
    assert get(old) == AUTO_DELETE_DELAY - 10 * H                        # trong 24h cuối: hiện thời gian còn lại
    assert get(old, now=NOW + 20 * H) == timedelta(0)                    # quá mốc: 0 (chờ lượt dọn kế tiếp)
    assert get(done, Settings(auto_delete_done=False)) is None           # công tắc tắt: ẩn
    assert get(old, Settings(auto_delete_overdue=False)) is None


# ------------------------------------------------------------------ Migration v6
def make_v5(path):
    raw = sqlite3.connect(path)
    raw.executescript("\n".join(db.MIGRATIONS[:5]) + "\nPRAGMA user_version = 5;")
    raw.execute("INSERT INTO items (kind,title,starts_at,status,created_at,updated_at,completed_at) VALUES "
                "('deadline','xong cũ','2026-01-01T09:00:00','done','2026-01-01T08:00:00','2026-01-02T08:00:00',"
                "'2026-01-02T08:00:00')")
    raw.execute("INSERT INTO items (kind,title,starts_at,status,created_at,updated_at) VALUES "
                "('deadline','trễ cũ','2026-01-01T09:00:00','todo','2026-01-01T08:00:00','2026-01-01T08:00:00')")
    raw.execute("INSERT INTO items (kind,title,starts_at,ends_at,created_at,updated_at) VALUES "
                "('event','họp','2026-01-01T09:00:00','2026-01-01T10:00:00','2026-01-01T08:00:00','2026-01-01T08:00:00')")
    raw.commit()
    raw.close()


def test_migration_v6_backfills_fresh_day_not_instant_deletion(tmp_path):
    path = tmp_path / "norilan.db"
    make_v5(path)
    before = datetime.now().replace(microsecond=0)
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 6
    repo = Repository(conn)
    deadlines = repo.list_deadlines()
    assert len(deadlines) == 2
    for d in deadlines:                                                  # mốc = lúc nâng cấp (không phải ngày cũ)
        stamp = d.completed_at if d.is_done else d.due_changed_at
        assert before - timedelta(seconds=5) <= stamp <= datetime.now() + timedelta(seconds=5)
    assert select_expired(deadlines, datetime.now(), ALL_ON) == []       # không bị xoá ngay sau khi nâng cấp
    assert ids(select_expired(deadlines, datetime.now() + 25 * H, ALL_ON)) == ["trễ cũ", "xong cũ"]
    assert repo.count_items()["event"] == 1                              # Event giữ nguyên
    assert list((tmp_path / "backups").glob("truoc-khi-nang-cap-v5-*.zip"))
    conn.close()
    connect(path).close()                                                # idempotent
    assert len(list((tmp_path / "backups").glob("*.zip"))) == 1
