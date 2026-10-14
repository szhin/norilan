from __future__ import annotations

import pytest

from app.files import (
    AttachmentMissingError,
    FileService,
    attachment_from_target,
    is_app_copy,
    is_missing,
    looks_like_url,
    nearest_existing_dir,
    prepare_attachments,
    remove_orphan_copies,
    unique_path,
)
from app.models import Attachment, AttachmentKind


@pytest.fixture
def docx(tmp_path):
    path = tmp_path / "BaiTap" / "Lab 2.docx"
    path.parent.mkdir()
    path.write_text("x")
    return path


@pytest.mark.parametrize(
    ("text", "expected"),
    [("https://lms.edu/x", True), ("http://a.b", True), ("www.google.com", True),
     (r"C:\Users\a\b.docx", False), ("/home/a/b.pdf", False), ("bai tap.docx", False)],
)
def test_looks_like_url(text, expected):
    assert looks_like_url(text) is expected


def test_attachment_from_target_detects_kind(docx):
    assert attachment_from_target(str(docx)).kind is AttachmentKind.FILE
    assert attachment_from_target(str(docx.parent)).kind is AttachmentKind.FOLDER
    link = attachment_from_target("  www.lms.edu/lab2 ")
    assert link.kind is AttachmentKind.URL and link.target == "https://www.lms.edu/lab2"


def test_attachment_from_target_strips_windows_copy_as_path_quotes(docx):
    assert attachment_from_target(f'"{docx}"').target == str(docx)


def test_attachment_from_target_rejects_empty():
    with pytest.raises(ValueError):
        attachment_from_target('  ""  ')


def test_is_missing(docx, tmp_path):
    assert not is_missing(Attachment(str(docx)))
    assert is_missing(Attachment(str(tmp_path / "mat.docx")))
    assert is_missing(Attachment(str(docx), AttachmentKind.FOLDER))  # là file, không phải thư mục
    assert not is_missing(Attachment("https://x.y", AttachmentKind.URL))


def test_nearest_existing_dir(docx):
    assert nearest_existing_dir(str(docx.parent / "a" / "b" / "c.docx")) == docx.parent


def test_unique_path(tmp_path):
    (tmp_path / "bai.docx").write_text("1")
    (tmp_path / "bai (1).docx").write_text("2")
    assert unique_path(tmp_path, "bai.docx").name == "bai (2).docx"
    assert unique_path(tmp_path, "moi.docx").name == "moi.docx"


def test_prepare_attachments_copies_new_and_existing_files(docx, tmp_path):
    dest = tmp_path / "appdata"
    new_file = Attachment(str(docx))
    old_file = Attachment(str(docx), id=7)                 # đính kèm từ trước cũng được sao chép
    folder = Attachment(str(docx.parent), AttachmentKind.FOLDER)
    missing = Attachment(str(tmp_path / "mat.docx"))
    errors = prepare_attachments([new_file, old_file, folder, missing], copy_files=True, dest_dir=dest)
    assert errors == []
    assert new_file.target == str(dest / "Lab 2.docx")
    assert old_file.target == str(dest / "Lab 2 (1).docx")
    assert folder.target == str(docx.parent) and missing.target.endswith("mat.docx")
    assert is_app_copy(new_file, dest) and not is_app_copy(folder, dest)


def test_prepare_attachments_does_not_copy_a_copy_twice(docx, tmp_path):
    dest = tmp_path / "appdata"
    att = Attachment(str(docx))
    prepare_attachments([att], copy_files=True, dest_dir=dest)
    first = att.target
    prepare_attachments([att], copy_files=True, dest_dir=dest)
    assert att.target == first and len(list(dest.iterdir())) == 1


def test_copy_survives_deleting_original(docx, tmp_path, launcher):
    dest = tmp_path / "appdata"
    att = Attachment(str(docx))
    prepare_attachments([att], copy_files=True, dest_dir=dest)
    docx.unlink()                                      # xoá file gốc
    FileService(launcher).open(att)                    # vẫn mở được bản sao
    assert launcher.opened == [str(dest / "Lab 2.docx")]


def test_remove_orphan_copies_only_touches_unreferenced_files(tmp_path, docx):
    dest = tmp_path / "appdata"
    dest.mkdir()
    kept, orphan = dest / "giu.docx", dest / "bo.docx"
    kept.write_text("1")
    orphan.write_text("2")
    removed = remove_orphan_copies(dest, {str(kept), str(docx)})
    assert removed == [orphan] and kept.exists() and not orphan.exists() and docx.exists()
    assert remove_orphan_copies(tmp_path / "chua_co", set()) == []


def test_prepare_attachments_disabled_keeps_paths(docx, tmp_path):
    att = Attachment(str(docx))
    prepare_attachments([att], copy_files=False, dest_dir=tmp_path / "d")
    assert att.target == str(docx)


def test_file_service_open(docx, launcher):
    service = FileService(launcher)
    service.open(Attachment(str(docx)))
    service.open(Attachment("https://lms.edu", AttachmentKind.URL))
    assert launcher.opened == [str(docx), "https://lms.edu"]


def test_file_service_open_missing_raises(tmp_path, launcher):
    att = Attachment(str(tmp_path / "mat.docx"))
    with pytest.raises(AttachmentMissingError) as info:
        FileService(launcher).open(att)
    assert info.value.attachment is att
    assert launcher.opened == []


def test_file_service_reveal(docx, launcher):
    service = FileService(launcher)
    service.reveal(Attachment(str(docx)))
    service.reveal(Attachment(str(docx.parent), AttachmentKind.FOLDER))
    opened = service.reveal(Attachment(str(docx.parent / "da_xoa.docx")))
    assert launcher.revealed == [(str(docx), True), (str(docx.parent), False), (str(docx.parent), False)]
    assert opened == str(docx.parent)
    with pytest.raises(ValueError):
        service.reveal(Attachment("https://x", AttachmentKind.URL))
