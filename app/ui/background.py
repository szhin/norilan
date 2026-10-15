"""Ảnh nền: nhập ảnh vào thư mục app (thu nhỏ nếu quá lớn), làm mờ, và widget vẽ ảnh phủ kín cửa sổ."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget

from app import paths
from app.i18n import tr

log = logging.getLogger(__name__)

MAX_SIDE = 2560          # ảnh lớn hơn sẽ được thu nhỏ khi nhập (nhẹ RAM, vẽ nhanh)
IMAGE_PATTERNS = "*.png *.jpg *.jpeg *.bmp *.webp *.gif"


class BackgroundError(ValueError):
    pass


def image_filter() -> str:
    """Bộ lọc cho hộp thoại chọn ảnh, vd. 'Ảnh (*.png *.jpg …)'."""
    return f"{tr('prompt.images')} ({IMAGE_PATTERNS})"


def import_background(source: str | Path) -> str:
    """Chép ảnh vào thư mục backgrounds của app. Trả về tên file mới (lưu trong settings)."""
    image = QImage(str(source))
    if image.isNull():
        raise BackgroundError(tr("bg.err_unreadable"))
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    has_alpha = image.hasAlphaChannel()
    name = f"bg-{uuid.uuid4().hex[:10]}.{'png' if has_alpha else 'jpg'}"
    target = paths.backgrounds_dir() / name
    if not image.save(str(target), "PNG" if has_alpha else "JPG", -1 if has_alpha else 90):
        raise BackgroundError(tr("bg.err_save"))
    log.info("Imported background %s → %s", source, name)
    return name


def background_path(name: str) -> Path | None:
    if not name:
        return None
    path = paths.backgrounds_dir() / name
    return path if path.is_file() else None


def soften(image: QImage, factor: int = 28) -> QImage:
    """Làm mờ nhanh: thu nhỏ rồi phóng to lại có nội suy (đủ mịn cho ảnh nền)."""
    w, h = image.width(), image.height()
    small = image.scaled(max(1, w // factor), max(1, h // factor), Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    middle = small.scaled(max(1, w // 4), max(1, h // 4), Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return middle.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def load_background(name: str, *, blur: bool = False) -> QPixmap | None:
    path = background_path(name)
    if path is None:
        return None
    image = QImage(str(path))
    if image.isNull():
        log.warning("Ảnh nền %s không đọc được", path)
        return None
    return QPixmap.fromImage(soften(image) if blur else image)


def cleanup_backgrounds(keep: set[str]) -> None:
    """Xoá ảnh nền cũ không còn dùng (vd. sau khi đổi ảnh khác)."""
    for file in paths.backgrounds_dir().iterdir():
        if file.is_file() and file.name not in keep:
            try:
                file.unlink()
            except OSError:
                log.warning("Không xoá được ảnh nền cũ %s", file)


class BackgroundWidget(QWidget):
    """Widget gốc của cửa sổ: tô màu nền theme, rồi vẽ ảnh nền phủ kín (cắt giữa, không méo)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Central")
        self._color = QColor("#ffffff")
        self._pixmap: QPixmap | None = None
        self._cache: QPixmap | None = None
        self._cache_size = QSize()

    def set_background(self, pixmap: QPixmap | None, color: str) -> None:
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._color = QColor(color)
        self._cache = None
        self.update()

    def has_image(self) -> bool:
        return self._pixmap is not None

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        if self._pixmap is None:
            return
        if self._cache is None or self._cache_size != self.size():
            ratio = self.devicePixelRatioF()
            target = QSize(round(self.width() * ratio), round(self.height() * ratio))
            self._cache = self._pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation)
            self._cache.setDevicePixelRatio(ratio)
            self._cache_size = self.size()
        logical = self._cache.deviceIndependentSize()
        x = round((self.width() - logical.width()) / 2)
        y = round((self.height() - logical.height()) / 2)
        painter.drawPixmap(QRect(x, y, round(logical.width()), round(logical.height())), self._cache)
