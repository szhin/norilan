"""Icon của app (vẽ bằng code, theo màu nhấn) – dùng cho cửa sổ, khay hệ thống và toast."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_icon(size: int, color: str = "#2383e2") -> QPixmap:
    """Ô vuông bo góc màu nhấn, bên trong là tờ lịch trắng có dấu ✓."""
    accent = QColor(color)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0, accent.lighter(118))
    gradient.setColorAt(1, accent.darker(112))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.23, size * 0.23)

    margin = size * 0.2
    body = QRectF(margin, margin * 1.1, size - 2 * margin, size - 2.05 * margin)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(body, size * 0.07, size * 0.07)
    band = QRectF(body.left(), body.top(), body.width(), body.height() * 0.26)
    clip = QPainterPath()
    clip.addRoundedRect(body, size * 0.07, size * 0.07)
    p.setClipPath(clip)
    p.setBrush(accent.darker(125))
    p.drawRect(band)
    p.setClipping(False)

    pen = QPen(accent.darker(110), max(1.4, size * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = body.left(), band.bottom(), body.width(), body.bottom() - band.bottom()
    check = QPainterPath(QPointF(x + w * 0.27, y + h * 0.52))
    check.lineTo(QPointF(x + w * 0.45, y + h * 0.72))
    check.lineTo(QPointF(x + w * 0.75, y + h * 0.32))
    p.drawPath(check)
    p.end()
    return pixmap


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
