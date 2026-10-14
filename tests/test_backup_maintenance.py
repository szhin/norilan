from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta

import pytest

from app.backup import BackupError, create_backup, default_backup_name, prune_backups, read_manifest, restore_backup
from app.db import SCHEMA_VERSION, connect
from app.maintenance import PURGE_STATE_KEY, count_purgeable, purge_done
from app.models import Attachment, Event, Task, TaskStatus
from app.repository import Repository

NOW = datetime(2026, 10, 14, 10, 0)


# ------------------------------------------------------------------ Ngày hoàn thành
def test_completed_at_set_kept_and_cleared(repo):
    task = repo.add_task(Task("A", NOW))
    assert task.completed_at is None
    repo.set_task_status(task.id, TaskStatus.DONE, at=NOW)
    assert repo.get_task(task.id).completed_at == NOW

    later = repo.get_task(task.id)
    later.title = "A (đổi tên)"
    assert repo.update_task(later).completed_at == NOW         # sửa tên: giữ mốc hoàn thành
    repo.set_task_status(task.id, TaskStatus.DONE, at=NOW + timedelta(days=3))
    assert repo.get_task(task.id).completed_at == NOW         # đánh dấu xong lần nữa: vẫn giữ mốc cũ

    reopened = repo.get_task(task.id)
    reopened.status = TaskStatus.IN_PROGRESS
    assert repo.update_task(reopened).completed_at is None    # mở lại: xoá mốc
    assert repo.add_task(Task("B", NOW, status=TaskStatus.DONE)).completed_at is not None


def test_migration_v3_backfills_completed_at(tmp_path):
    import sqlite3

    from app import db

    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    raw.executescript(db.MIGRATIONS[0] + db.MIGRATIONS[1] + "\nPRAGMA user_version = 2;")
    raw.execute("INSERT INTO items (kind,title,starts_at,status,created_at,updated_at) "
                "VALUES ('task','Xong cũ','2026-09-01T09:00:00','done','2026-08-30T08:00:00','2026-09-02T08:00:00')")
    raw.commit()
    raw.close()
    task = Repository(connect(path)).search("Xong cũ")[0]
    assert task.completed_at == datetime(2026, 9, 2, 8, 0)


# ------------------------------------------------------------------ Tự xoá
@pytest.fixture
def done_tasks(repo):
    ids = {}
    for name, days_ago in (("cũ", 40), ("vừa", 10), ("mới", 1)):
        task = repo.add_task(Task(name, NOW - timedelta(days=days_ago)))
        repo.set_task_status(task.id, TaskStatus.DONE, at=NOW - timedelta(days=days_ago))
        ids[name] = task.id
    ids["chưa xong"] = repo.add_task(Task("chưa xong", NOW - timedelta(days=60))).id
    ids["sự kiện"] = repo.add_event(Event("họp", NOW - timedelta(days=60), NOW - timedelta(days=60))).id
    return ids


def test_purge_only_old_done_tasks(repo, done_tasks, tmp_path):
    assert count_purgeable(repo, 30, NOW) == 1
    assert count_purgeable(repo, 7, NOW) == 2
    assert count_purgeable(repo, 0, NOW) == 0
    assert purge_done(repo, 30, NOW, attachments_dir=tmp_path) == 1
    assert repo.get_item(done_tasks["cũ"]) is None
    for keep in ("vừa", "mới", "chưa xong", "sự kiện"):
        assert repo.get_item(done_tasks[keep]) is not None


def test_purge_runs_once_per_day_unless_forced(repo, done_tasks, tmp_path):
    assert purge_done(repo, 30, NOW, attachments_dir=tmp_path) == 1
    assert purge_done(repo, 7, NOW + timedelta(hours=1), attachments_dir=tmp_path) == 0   # đã chạy hôm nay
    assert purge_done(repo, 7, NOW + timedelta(hours=1), attachments_dir=tmp_path, force=True) == 1
    assert repo.get_state(PURGE_STATE_KEY) == NOW.date().isoformat()


def test_purge_disabled(repo, done_tasks, tmp_path):
    assert purge_done(repo, 0, NOW, attachments_dir=tmp_path, force=True) == 0
    assert repo.count_items()["task"] == 4


def test_purge_cleans_attachment_copies_but_not_originals(repo, tmp_path):
    copies = tmp_path / "attachments"
    copies.mkdir()
    copy, original = copies / "bai.docx", tmp_path / "goc.docx"
    copy.write_text("x")
    original.write_text("y")
    task = repo.add_task(Task("A", NOW, attachments=[Attachment(str(copy)), Attachment(str(original))]))
    repo.set_task_status(task.id, TaskStatus.DONE, at=NOW - timedelta(days=40))
    purge_done(repo, 30, NOW, attachments_dir=copies)
    assert not copy.exists() and original.exists()


# ------------------------------------------------------------------ Sao lưu / khôi phục
@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "data"
    (root / "attachments").mkdir(parents=True)
    (root / "backgrounds").mkdir()
    (root / "attachments" / "lab.docx").write_text("bài làm")
    (root / "backgrounds" / "bg.jpg").write_bytes(b"\xff\xd8anh")
    (root / "settings.json").write_text(json.dumps({"theme": "dark", "accent": "green"}), encoding="utf-8")
    return root


def test_backup_and_restore_roundtrip(tmp_path, data_dir):
    conn = connect(data_dir / "norilan.db")
    repo = Repository(conn)
    repo.add_task(Task("Lab 2", NOW, subject="PPT", attachments=[Attachment(str(data_dir / "attachments" / "lab.docx"))]))
    repo.set_subject_color("PPT", "purple")
    backup = create_backup(conn, tmp_path / "out" / default_backup_name(NOW), data_dir=data_dir, now=NOW)
    assert backup.name == "Norilan-saoluu-2026-10-14_1000.zip"
    manifest = read_manifest(backup)
    assert manifest["schema_version"] == SCHEMA_VERSION

    # Thay đổi sau khi sao lưu → khôi phục phải đưa về như cũ
    repo.add_task(Task("Việc mới", NOW))
    repo.delete_item(repo.search("Lab 2")[0].id)
    (data_dir / "attachments" / "lab.docx").unlink()
    (data_dir / "settings.json").write_text("{}", encoding="utf-8")

    restore_backup(conn, backup, data_dir=data_dir)
    titles = [t.title for t in repo.search("")]
    assert titles == ["Lab 2"]
    assert repo.subject_colors() == {"PPT": "purple"}
    assert (data_dir / "attachments" / "lab.docx").read_text() == "bài làm"
    assert json.loads((data_dir / "settings.json").read_text())["theme"] == "dark"
    assert (data_dir / "backgrounds" / "bg.jpg").exists()


def test_restore_on_another_computer_rewrites_copy_paths(tmp_path, data_dir):
    conn = connect(data_dir / "norilan.db")
    Repository(conn).add_task(Task("Lab", NOW, attachments=[
        Attachment(str(data_dir / "attachments" / "lab.docx")), Attachment("/home/ai/goc.pdf")]))
    backup = create_backup(conn, tmp_path / "b.zip", data_dir=data_dir, now=NOW)

    other = tmp_path / "may-khac"
    other.mkdir()
    other_conn = connect(other / "norilan.db")
    restore_backup(other_conn, backup, data_dir=other)
    targets = [a.target for a in Repository(other_conn).search("Lab")[0].attachments]
    assert targets == [str(other / "attachments" / "lab.docx"), "/home/ai/goc.pdf"]
    assert (other / "attachments" / "lab.docx").exists()


def test_reject_non_backup_and_newer_backup(tmp_path):
    not_zip = tmp_path / "a.zip"
    not_zip.write_text("không phải zip")
    with pytest.raises(BackupError, match="không phải .zip"):
        read_manifest(not_zip)
    random_zip = tmp_path / "b.zip"
    with zipfile.ZipFile(random_zip, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(BackupError, match="không phải bản sao lưu"):
        read_manifest(random_zip)
    newer = tmp_path / "c.zip"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr("manifest.json", json.dumps({"app": "Norilan", "schema_version": SCHEMA_VERSION + 1}))
        z.writestr("norilan.db", b"")
    with pytest.raises(BackupError, match="mới hơn"):
        read_manifest(newer)


def test_restore_ignores_zip_slip_and_rejects_corrupt_db(tmp_path, data_dir):
    conn = connect(data_dir / "norilan.db")
    Repository(conn).add_task(Task("Giữ nguyên", NOW))
    evil = tmp_path / "evil.zip"
    good_backup = create_backup(conn, tmp_path / "good.zip", data_dir=data_dir, now=NOW)
    with zipfile.ZipFile(good_backup) as src, zipfile.ZipFile(evil, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr("../../thoat_ra_ngoai.txt", "x")
        dst.writestr("attachments/../../cung_thoat.txt", "x")
    restore_backup(conn, evil, data_dir=data_dir)
    assert not (tmp_path / "thoat_ra_ngoai.txt").exists() and not (tmp_path.parent / "thoat_ra_ngoai.txt").exists()
    assert not (tmp_path / "cung_thoat.txt").exists()

    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(corrupt, "w") as z:
        z.writestr("manifest.json", json.dumps({"app": "Norilan", "schema_version": 1}))
        z.writestr("norilan.db", b"day khong phai sqlite" * 100)
    with pytest.raises(BackupError, match="hỏng"):
        restore_backup(conn, corrupt, data_dir=data_dir)
    assert Repository(conn).search("Giữ nguyên")  # dữ liệu cũ không bị đụng tới


def test_prune_backups_keeps_newest(tmp_path):
    import os
    import time

    for i in range(7):
        f = tmp_path / f"b{i}.zip"
        f.write_text(str(i))
        os.utime(f, (time.time() + i, time.time() + i))
    prune_backups(tmp_path, keep=5)
    assert sorted(f.name for f in tmp_path.iterdir()) == [f"b{i}.zip" for i in range(2, 7)]
