"""StudyTime: migration v3 → v4, CRUD, quy tắc liên kết / xoá với Deadline, backup cũ."""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import db
from app.backup import create_backup, restore_backup
from app.db import SCHEMA_VERSION, connect, schema_version
from app.models import Attachment, Deadline, DeadlineStatus, Event, StudyTime, ValidationError
from app.repository import NotFoundError, Repository

NOW = datetime(2026, 10, 14, 10, 0)
DUE = datetime(2026, 10, 20, 23, 59)


def make_v3_db(path: Path) -> None:
    """DB đúng như bản 0.5.0 (schema v3): 1 task có đính kèm, 1 task xong, 1 event."""
    raw = sqlite3.connect(path)
    raw.executescript("\n".join(db.MIGRATIONS[:3]) + "\nPRAGMA user_version = 3;")
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("INSERT INTO items (kind,title,starts_at,status,priority,subject,remind_before_minutes,"
                "created_at,updated_at) VALUES ('task','Lab 2','2026-10-20T23:59:00','todo',2,'Toán',30,'2026-10-01T08:00:00','2026-10-01T08:00:00')")
    raw.execute("INSERT INTO items (kind,title,starts_at,status,created_at,updated_at,completed_at) "
                "VALUES ('task','Xong','2026-10-01T09:00:00','done','2026-10-01T08:00:00','2026-10-01T08:00:00','2026-10-02T08:00:00')")
    raw.execute("INSERT INTO items (kind,title,starts_at,ends_at,location,color,created_at,updated_at) "
                "VALUES ('event','Họp','2026-10-15T09:00:00','2026-10-15T10:00:00','Phòng 1','green','2026-10-01T08:00:00','2026-10-01T08:00:00')")
    raw.execute("INSERT INTO attachments (item_id,kind,target,display_name,position,created_at) "
                "VALUES (1,'url','https://x.edu','LMS',0,'2026-10-01T08:00:00')")
    raw.execute("INSERT INTO subject_colors VALUES ('Toán','purple')")
    raw.commit()
    raw.close()


# ------------------------------------------------------------------ Migration
def test_migration_v4_converts_old_database(tmp_path):
    path = tmp_path / "norilan.db"
    make_v3_db(path)
    conn = connect(path)
    assert schema_version(conn) == SCHEMA_VERSION == 7
    repo = Repository(conn)
    lab = repo.get_deadline(1)
    assert lab is not None and (lab.title, lab.priority, lab.subject, lab.remind_before_minutes) == ("Lab 2", 2, "Toán", 30)
    assert [a.display_name for a in lab.attachments] == ["LMS"]            # đính kèm không bị xoá dây chuyền
    done = repo.get_deadline(2)
    # v6 đặt lại completed_at = lúc nâng cấp (để deadline cũ có đủ 24 giờ trước khi tự xoá)
    assert done.is_done and abs(done.completed_at - datetime.now()) < timedelta(minutes=1)
    event = repo.get_event(3)
    assert (event.location, event.color) == ("Phòng 1", "green")
    assert repo.subject_colors() == {"Toán": "purple"}
    assert repo.count_items() == {"deadline": 2, "event": 1, "study": 0, "class": 0}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1          # bật lại sau migrate
    # AUTOINCREMENT giữ nguyên: id mới không trùng id cũ
    assert repo.add_study_time(StudyTime("S", NOW, NOW + timedelta(hours=1))).id == 4
    conn.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "norilan.db"
    make_v3_db(path)
    connect(path).close()
    conn = connect(path)                       # mở lần hai: không migrate, không sao lưu thêm
    db.migrate(conn)
    assert schema_version(conn) == SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
    assert len(list((tmp_path / "backups").glob("*.zip"))) == 1
    conn.close()


def test_auto_backup_before_migration(tmp_path):
    path = tmp_path / "norilan.db"
    make_v3_db(path)
    connect(path).close()
    (backup,) = (tmp_path / "backups").glob(f"{db.PRE_MIGRATE_PREFIX}-v3-*.zip")
    with zipfile.ZipFile(backup) as archive:
        assert '"schema_version": 3' in archive.read("manifest.json").decode("utf-8")
        extracted = tmp_path / "x.db"
        extracted.write_bytes(archive.read("norilan.db"))
    old = sqlite3.connect(extracted)
    assert old.execute("SELECT COUNT(*) FROM items WHERE kind = 'task'").fetchone()[0] == 2   # bản gốc còn nguyên
    assert old.execute("PRAGMA user_version").fetchone()[0] == 3
    old.close()


def test_fresh_database_makes_no_backup(tmp_path):
    connect(tmp_path / "norilan.db").close()
    assert not (tmp_path / "backups").exists()


def test_migration_aborts_on_foreign_key_violation(tmp_path):
    path = tmp_path / "norilan.db"
    make_v3_db(path)
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO attachments (item_id,kind,target,position,created_at) VALUES (999,'url','x',0,'2026-10-01T08:00:00')")
    raw.commit()
    raw.close()
    with pytest.raises(sqlite3.IntegrityError):
        connect(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 3          # rollback: vẫn là v3
    assert raw.execute("SELECT COUNT(*) FROM items WHERE kind = 'task'").fetchone()[0] == 2
    raw.close()


def test_restore_old_backup_is_migrated_and_new_backup_has_new_schema(tmp_path):
    old_path = tmp_path / "old.db"
    make_v3_db(old_path)
    old = sqlite3.connect(old_path)
    zip_path = create_backup(old, tmp_path / "old.zip", data_dir=tmp_path, now=NOW)
    old.close()

    conn = connect(":memory:")
    repo = Repository(conn)
    repo.add_event(Event("Sẽ bị thay", NOW, NOW + timedelta(hours=1)))
    restore_backup(conn, zip_path, data_dir=tmp_path / "data")
    assert schema_version(conn) == SCHEMA_VERSION
    assert repo.count_items() == {"deadline": 2, "event": 1, "study": 0, "class": 0}
    assert [a.display_name for a in repo.get_deadline(1).attachments] == ["LMS"]
    repo.add_study_time(StudyTime("Học", NOW, NOW + timedelta(hours=1), deadline_id=1))

    new_zip = create_backup(conn, tmp_path / "new.zip", data_dir=tmp_path / "data", now=NOW)
    with zipfile.ZipFile(new_zip) as archive:
        assert f'"schema_version": {SCHEMA_VERSION}' in archive.read("manifest.json").decode("utf-8")
        copy = tmp_path / "new.db"
        copy.write_bytes(archive.read("norilan.db"))
    check = sqlite3.connect(copy)
    assert check.execute("SELECT COUNT(*) FROM items WHERE kind = 'study' AND deadline_id = 1").fetchone()[0] == 1
    check.close()
    conn.close()


# ------------------------------------------------------------------ CRUD & validation
def test_study_time_roundtrip_and_update(repo):
    study = repo.add_study_time(StudyTime("  Học Tiếng Anh ", NOW, NOW + timedelta(hours=2), subject=" Anh ", notes="n"))
    assert (study.title, study.subject, study.deadline_id, study.notes) == ("Học Tiếng Anh", "Anh", None, "n")
    assert study.ends_at - study.starts_at == timedelta(hours=2)
    study.title = "Học từ vựng"
    study.ends_at = NOW + timedelta(hours=3)
    updated = repo.update_study_time(study)
    assert updated.title == "Học từ vựng" and updated.ends_at == NOW + timedelta(hours=3)
    assert repo.get_study_time(study.id) == updated
    assert repo.get_deadline(study.id) is None and repo.get_event(study.id) is None   # đúng loại
    assert "Anh" in repo.subjects()


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(minutes=-5)])
def test_study_time_end_must_be_after_start(repo, delta):
    with pytest.raises(ValidationError):
        repo.add_study_time(StudyTime("X", NOW, NOW + delta))
    with pytest.raises(ValidationError):
        StudyTime("", NOW, NOW + timedelta(hours=1)).validate()


def test_study_time_must_link_to_existing_deadline(repo):
    with pytest.raises(ValidationError):
        repo.add_study_time(StudyTime("X", NOW, NOW + timedelta(hours=1), deadline_id=999))
    event = repo.add_event(Event("E", NOW, NOW + timedelta(hours=1)))
    with pytest.raises(ValidationError):                              # event không phải deadline
        repo.add_study_time(StudyTime("X", NOW, NOW + timedelta(hours=1), deadline_id=event.id))
    with pytest.raises(NotFoundError):
        repo.update_study_time(StudyTime("X", NOW, NOW + timedelta(hours=1), id=999))


def test_study_time_appears_in_calendar_search_upcoming(repo):
    study = repo.add_study_time(StudyTime("Ôn Giải tích", NOW + timedelta(hours=1), NOW + timedelta(hours=3), subject="Toán"))
    assert study in repo.items_on(NOW.date())
    assert study in repo.upcoming(NOW)
    assert repo.search("giai tich") == [study]
    assert repo.search("toan", kind="study") == [study]
    assert repo.search("giai tich", kind="deadline") == []


# ------------------------------------------------------------------ Liên kết & xoá
@pytest.fixture
def linked(repo):
    deadline = repo.add_deadline(Deadline("Lab 2", DUE, subject="Toán"))
    s1 = repo.add_study_time(StudyTime("Lab 2", NOW, NOW + timedelta(hours=1), deadline_id=deadline.id))
    s2 = repo.add_study_time(StudyTime("Lab 2 (tiếp)", NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1),
                                       deadline_id=deadline.id))
    alone = repo.add_study_time(StudyTime("Học Tiếng Anh", NOW, NOW + timedelta(hours=1)))
    return deadline, s1, s2, alone


def test_deadline_has_many_study_times_and_count(repo, linked):
    deadline, s1, s2, alone = linked
    assert [s.id for s in repo.study_times_for(deadline.id)] == [s1.id, s2.id]
    assert repo.get_deadline(deadline.id).study_count == 2
    assert repo.open_deadlines()[0].study_count == 2
    lonely = repo.add_deadline(Deadline("Trống", DUE))
    assert repo.get_deadline(lonely.id).study_count == 0
    assert alone.deadline_id is None


def test_open_deadlines_excludes_done_and_orders_by_due(repo):
    late = repo.add_deadline(Deadline("late", DUE + timedelta(days=3)))
    soon = repo.add_deadline(Deadline("soon", DUE))
    repo.add_deadline(Deadline("done", DUE, status=DeadlineStatus.DONE))
    assert [d.id for d in repo.open_deadlines()] == [soon.id, late.id]


def test_delete_deadline_unlink_keeps_study_times(repo, linked):
    deadline, s1, s2, alone = linked
    assert repo.delete_deadline(deadline.id, study_times="unlink") is True
    assert repo.get_deadline(deadline.id) is None
    assert repo.get_study_time(s1.id).deadline_id is None and repo.get_study_time(s2.id).deadline_id is None
    assert repo.count_items()["study"] == 3


def test_delete_deadline_with_study_times(repo, linked):
    deadline, s1, s2, alone = linked
    assert repo.delete_deadline(deadline.id, study_times="delete") is True
    assert repo.get_study_time(s1.id) is None and repo.get_study_time(s2.id) is None
    assert repo.get_study_time(alone.id) is not None               # Study time độc lập không bị đụng tới
    with pytest.raises(ValueError):
        repo.delete_deadline(1, study_times="whatever")


def test_generic_delete_item_also_unlinks(repo, linked):
    deadline, s1, *_ = linked
    repo.delete_item(deadline.id)
    assert repo.get_study_time(s1.id).deadline_id is None


def test_deleting_study_time_never_affects_deadline(repo, linked):
    deadline, s1, s2, _ = linked
    assert repo.delete_item(s1.id) is True
    survivor = repo.get_deadline(deadline.id)
    assert survivor is not None and survivor.study_count == 1


def test_bulk_delete_deadlines_unlinks_study_times(repo, linked):
    deadline, s1, *_ = linked
    assert repo.delete_deadlines([deadline.id]) == 1                   # mặc định 'unlink' (dùng cho tự xoá)
    assert repo.get_study_time(s1.id).deadline_id is None


def test_db_rejects_deadline_id_on_non_study_rows(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO items (kind,title,starts_at,status,deadline_id,created_at,updated_at) "
                     "VALUES ('deadline','x','2026-10-15T09:00:00','todo',1,'2026-10-01T08:00:00','2026-10-01T08:00:00')")
