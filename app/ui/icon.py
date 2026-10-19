"""Icon của app (ảnh app/assets/icon.png) – dùng cho cửa sổ, khay hệ thống và toast."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QIcon, QPixmap

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


ICON_FILE = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def render_icon(size: int, color: str = "") -> QPixmap:
    """Icon của Norilan (ảnh app/assets/icon.png) thu về `size`. `color` giữ lại cho tương thích, không còn dùng."""
    source = QPixmap(str(ICON_FILE))
    return source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def app_icon(color: str = "#2383e2") -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(render_icon(size, color))
    return icon


def save_icon_png(path: Path, color: str = "#2383e2", size: int = 256) -> Path:
    """Ghi icon ra file PNG (toast Windows cần đường dẫn file ảnh)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    render_icon(size, color).save(str(path), "PNG")
    return path


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png_bytes(size: int, color: str) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    render_icon(size, color).save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write_ico(path: Path, color: str = "#2383e2", sizes: tuple[int, ...] = ICO_SIZES) -> Path:
    """File .ico nhiều kích thước (ảnh PNG bên trong – Windows Vista trở lên đều đọc được) cho Norilan.exe."""
    images = [(size, _png_bytes(size, color)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))           # reserved, type=icon, count
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size                     # 0 nghĩa là 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)
    return path
