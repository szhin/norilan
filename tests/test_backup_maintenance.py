from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta

import pytest

from app.backup import BackupError, create_backup, default_backup_name, prune_backups, read_manifest, restore_backup
from app.db import SCHEMA_VERSION, connect
from app.models import Attachment, Event, Deadline, DeadlineStatus
from app.repository import Repository

NOW = datetime(2026, 10, 14, 10, 0)


# ------------------------------------------------------------------ Ngày hoàn thành
def test_completed_at_set_kept_and_cleared(repo):
    task = repo.add_deadline(Deadline("A", NOW))
    assert task.completed_at is None
    repo.set_deadline_status(task.id, DeadlineStatus.DONE, at=NOW)
    assert repo.get_deadline(task.id).completed_at == NOW

    later = repo.get_deadline(task.id)
    later.title = "A (đổi tên)"
    assert repo.update_deadline(later).completed_at == NOW         # sửa tên: giữ mốc hoàn thành
    repo.set_deadline_status(task.id, DeadlineStatus.DONE, at=NOW + timedelta(days=3))
    assert repo.get_deadline(task.id).completed_at == NOW         # đánh dấu xong lần nữa: vẫn giữ mốc cũ

    reopened = repo.get_deadline(task.id)
    reopened.status = DeadlineStatus.IN_PROGRESS
    assert repo.update_deadline(reopened).completed_at is None    # mở lại: xoá mốc
    assert repo.add_deadline(Deadline("B", NOW, status=DeadlineStatus.DONE)).completed_at is not None


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
    # Bước v3 tự nó vẫn lấy lần sửa cuối làm mốc hoàn thành…
    raw = sqlite3.connect(path)
    raw.executescript(db.MIGRATIONS[2] + "; PRAGMA user_version = 3;")
    assert raw.execute("SELECT completed_at FROM items").fetchone()[0] == "2026-09-02T08:00:00"
    raw.close()
    # …nhưng v6 (tự xoá deadline) đặt lại mốc của deadline đã xong thành lúc nâng cấp, để chúng có đủ 24 giờ mới
    # thay vì bị xoá ngay (xem test_auto_clean.py).
    before = datetime.now().replace(microsecond=0)
    task = Repository(connect(path)).search("Xong cũ")[0]
    assert before - timedelta(seconds=5) <= task.completed_at <= datetime.now() + timedelta(seconds=5)


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
    repo.add_deadline(Deadline("Lab 2", NOW, subject="PPT", attachments=[Attachment(str(data_dir / "attachments" / "lab.docx"))]))
    repo.set_subject_color("PPT", "purple")
    backup = create_backup(conn, tmp_path / "out" / default_backup_name(NOW), data_dir=data_dir, now=NOW)
    assert backup.name == "Norilan-saoluu-2026-10-14_1000.zip"
    manifest = read_manifest(backup)
    assert manifest["schema_version"] == SCHEMA_VERSION

    # Thay đổi sau khi sao lưu → khôi phục phải đưa về như cũ
    repo.add_deadline(Deadline("Việc mới", NOW))
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
    Repository(conn).add_deadline(Deadline("Lab", NOW, attachments=[
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
    Repository(conn).add_deadline(Deadline("Giữ nguyên", NOW))
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
