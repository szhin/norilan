"""Hộp thoại hệ thống (chọn file, xác nhận, cảnh báo) gom về một chỗ.

Code UI luôn gọi qua module này (`prompts.confirm(...)`) để test có thể thay bằng hàm giả.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QDialog, QFileDialog, QInputDialog, QMessageBox, QWidget

from app import APP_NAME
from app.files import nearest_existing_dir
from app.i18n import tr
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
    paths, _ = QFileDialog.getOpenFileNames(parent, tr("prompt.pick_files"), _start_dir())
    if paths:
        _remember(paths[0])
    return paths


def pick_folder(parent: QWidget | None) -> str | None:
    path = QFileDialog.getExistingDirectory(parent, tr("prompt.pick_folder"), _start_dir())
    if path:
        _remember(path)
    return path or None


def pick_image(parent: QWidget | None) -> str | None:
    from app.ui.background import image_filter

    path, _ = QFileDialog.getOpenFileName(parent, tr("prompt.pick_image"), _start_dir(), image_filter())
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


def pick_color(parent: QWidget | None, start: str, title: str | None = None) -> str | None:
    color = QColorDialog.getColor(QColor(start), parent, title or tr("prompt.pick_accent"))
    return color.name() if color.isValid() else None


def pick_gradient(parent: QWidget | None, start: str | None = None) -> str | None:
    """Hộp thoại tạo màu gradient. Trả về 'gradient:#RRGGBB,#RRGGBB' hoặc None nếu huỷ."""
    from app.ui.color_picker import GradientDialog  # tránh vòng import

    dialog = GradientDialog(start, parent)
    return dialog.value() if dialog.exec() == QDialog.DialogCode.Accepted else None


def ask_link(parent: QWidget | None) -> str | None:
    text, ok = QInputDialog.getText(parent, tr("prompt.link_title"), tr("prompt.link_label"))
    return text.strip() if ok and text.strip() else None


def choose_replacement(parent: QWidget | None, att: Attachment) -> str | None:
    """Chọn lại đường dẫn cho đính kèm bị mất, mở sẵn ở thư mục cha gần nhất còn tồn tại."""
    start = _start_dir(nearest_existing_dir(att.target))
    if att.kind == AttachmentKind.FOLDER:
        path = QFileDialog.getExistingDirectory(parent, tr("prompt.relink_folder", name=att.name), start)
    else:
        suffix = Path(att.target).suffix
        all_files = f"{tr('prompt.all_files')} (*)"
        filters = f"{tr('prompt.same_type')} (*{suffix});;{all_files}" if suffix else all_files
        path, _ = QFileDialog.getOpenFileName(parent, tr("prompt.relink_file", name=att.name), start, filters)
    if path:
        _remember(path)
    return path or None


def ask_missing_action(parent: QWidget | None, att: Attachment) -> str:
    """Hỏi khi đính kèm bị mất. Trả về 'relink' | 'remove' | 'cancel'."""
    what = "folder" if att.kind == AttachmentKind.FOLDER else "file"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(tr(f"missing.title_{what}"))
    box.setText(tr(f"missing.text_{what}", name=att.name))
    box.setInformativeText(tr(f"missing.detail_{what}", target=att.target))
    relink = box.addButton(tr("missing.relink"), QMessageBox.ButtonRole.AcceptRole)
    remove = box.addButton(tr("missing.remove"), QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(tr("common.cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(relink)
    box.exec()
    clicked = box.clickedButton()
    if clicked is relink:
        return "relink"
    if clicked is remove:
        return "remove"
    return "cancel"


def confirm(parent: QWidget | None, title: str, text: str, *, ok_text: str | None = None) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text or tr("common.delete"), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(tr("common.cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def ask_study_after_deadline(parent: QWidget | None) -> bool:
    """Sau khi tạo Deadline mới: 'Đặt thêm thời gian để làm deadline này?' → True nếu chọn Có."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(tr("prompt.study_after_title"))
    box.setText(tr("prompt.study_after_text"))
    yes = box.addButton(tr("common.yes"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("common.no"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


def ask_delete_deadline_links(parent: QWidget | None, title: str, count: int) -> str:
    """Deadline còn Study time liên kết: 'Xoá luôn các Study time liên kết?' → 'delete' | 'unlink' | 'cancel'."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(tr("delete.links_title"))
    box.setText(tr("delete.links_text", title=title, count=count))
    delete = box.addButton(tr("delete.links_delete"), QMessageBox.ButtonRole.DestructiveRole)
    unlink = box.addButton(tr("delete.links_unlink"), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(tr("common.cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is delete:
        return "delete"
    if clicked is unlink:
        return "unlink"
    return "cancel"


def show_error(parent: QWidget | None, text: str, details: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(APP_NAME)
    box.setText(text)
    if details:
        box.setInformativeText(details)
    box.exec()
