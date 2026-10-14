"""Hộp thoại hệ thống (chọn file, xác nhận, cảnh báo) gom về một chỗ.

Code UI luôn gọi qua module này (`prompts.confirm(...)`) để test có thể thay bằng hàm giả.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QFileDialog, QInputDialog, QMessageBox, QWidget

from app import APP_NAME
from app.files import nearest_existing_dir
from app.models import Attachment, AttachmentKind

_last_dir: str | None = None


def _start_dir(hint: Path | None = None) -> str:
    if hint is not None:
        return str(hint)
    return _last_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)


def _remember(path: str) -> None:
    global _last_dir
    _last_dir = path if os.path.isdir(path) else os.path.dirname(path)


def pick_files(parent: QWidget | None) -> list[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, "Chọn file đính kèm", _start_dir())
    if paths:
        _remember(paths[0])
    return paths


def pick_folder(parent: QWidget | None) -> str | None:
    path = QFileDialog.getExistingDirectory(parent, "Chọn thư mục đính kèm", _start_dir())
    if path:
        _remember(path)
    return path or None


def pick_image(parent: QWidget | None) -> str | None:
    from app.ui.background import IMAGE_FILTER

    path, _ = QFileDialog.getOpenFileName(parent, "Chọn ảnh nền", _start_dir(), IMAGE_FILTER)
    if path:
        _remember(path)
    return path or None


def ask_save_path(parent: QWidget | None, title: str, default_path: str, file_filter: str) -> str | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, default_path, file_filter)
    return path or None


def ask_open_path(parent: QWidget | None, title: str, file_filter: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, title, _start_dir(), file_filter)
    return path or None


def show_info(parent: QWidget | None, text: str, details: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(APP_NAME)
    box.setText(text)
    if details:
        box.setInformativeText(details)
    box.exec()


def pick_color(parent: QWidget | None, start: str) -> str | None:
    color = QColorDialog.getColor(QColor(start), parent, "Chọn màu nhấn")
    return color.name() if color.isValid() else None


def ask_link(parent: QWidget | None) -> str | None:
    text, ok = QInputDialog.getText(parent, "Thêm link", "Dán đường link (vd. https://lms.truong.edu.vn/...):")
    return text.strip() if ok and text.strip() else None


def choose_replacement(parent: QWidget | None, att: Attachment) -> str | None:
    """Chọn lại đường dẫn cho đính kèm bị mất, mở sẵn ở thư mục cha gần nhất còn tồn tại."""
    start = _start_dir(nearest_existing_dir(att.target))
    if att.kind == AttachmentKind.FOLDER:
        path = QFileDialog.getExistingDirectory(parent, f"Chọn lại thư mục “{att.name}”", start)
    else:
        suffix = Path(att.target).suffix
        filters = f"Cùng loại (*{suffix});;Tất cả file (*)" if suffix else "Tất cả file (*)"
        path, _ = QFileDialog.getOpenFileName(parent, f"Chọn lại file “{att.name}”", start, filters)
    if path:
        _remember(path)
    return path or None


def ask_missing_action(parent: QWidget | None, att: Attachment) -> str:
    """Hỏi khi đính kèm bị mất. Trả về 'relink' | 'remove' | 'cancel'."""
    what = "thư mục" if att.kind == AttachmentKind.FOLDER else "file"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(f"Không tìm thấy {what}")
    box.setText(f"Không tìm thấy {what} “{att.name}”.")
    box.setInformativeText(
        f"Đường dẫn đã lưu:\n{att.target}\n\nCó thể {what} đã bị di chuyển, đổi tên hoặc xoá. "
        f"Bạn có muốn chọn lại vị trí mới không?"
    )
    relink = box.addButton("Chọn lại đường dẫn…", QMessageBox.ButtonRole.AcceptRole)
    remove = box.addButton("Gỡ đính kèm", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Huỷ", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(relink)
    box.exec()
    clicked = box.clickedButton()
    if clicked is relink:
        return "relink"
    if clicked is remove:
        return "remove"
    return "cancel"


def confirm(parent: QWidget | None, title: str, text: str, *, ok_text: str = "Xoá") -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Huỷ", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def show_error(parent: QWidget | None, text: str, details: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(APP_NAME)
    box.setText(text)
    if details:
        box.setInformativeText(details)
    box.exec()
