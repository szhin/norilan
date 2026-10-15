"""Ngôn ngữ giao diện phía Qt: lá cờ cho ô chọn ngôn ngữ, QLocale và bản dịch của chính Qt.

Cờ vẽ bằng SVG thay vì emoji vì Windows không hiển thị emoji cờ (🇻🇳 hiện thành hai chữ 'VN').
"""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import QByteArray, QCoreApplication, QLibraryInfo, QLocale, QRectF, Qt, QTranslator
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app import i18n

log = logging.getLogger(__name__)

# Ngôn ngữ → locale của Qt (tên tháng/thứ trong ô chọn ngày, bản dịch hộp thoại chọn màu/file…)
_QT_LOCALES = {
    "vi": (QLocale.Language.Vietnamese, QLocale.Country.Vietnam),
    "en": (QLocale.Language.English, QLocale.Country.UnitedKingdom),
    "es": (QLocale.Language.Spanish, QLocale.Country.Spain),
    "zh": (QLocale.Language.Chinese, QLocale.Country.China),
    "ja": (QLocale.Language.Japanese, QLocale.Country.Japan),
}
_translators: list[QTranslator] = []


def _star(cx: float, cy: float, r: float, point_deg: float = -90) -> str:
    """Toạ độ ngôi sao 5 cánh, một cánh chĩa theo góc `point_deg`."""
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.382
        angle = math.radians(point_deg + i * 36)
        points.append(f"{cx + radius * math.cos(angle):.2f},{cy + radius * math.sin(angle):.2f}")
    return " ".join(points)


def _china() -> str:
    small = "".join(
        f'<polygon fill="#ffde00" points="{_star(x, y, 1, math.degrees(math.atan2(5 - y, 5 - x)))}"/>'
        for x, y in ((10, 2), (12, 4), (12, 7), (10, 9))
    )
    return f'<rect width="30" height="20" fill="#de2910"/><polygon fill="#ffde00" points="{_star(5, 5, 3)}"/>{small}'


_UNION_JACK = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="20" viewBox="0 0 60 30" preserveAspectRatio="none">'
    '<clipPath id="t"><path d="M30,15 h30 v15 z v15 h-30 z h-30 v-15 z v-15 h30 z"/></clipPath>'
    '<rect width="60" height="30" fill="#012169"/>'
    '<path d="M0,0 L60,30 M60,0 L0,30" stroke="#fff" stroke-width="6"/>'
    '<path d="M0,0 L60,30 M60,0 L0,30" clip-path="url(#t)" stroke="#c8102e" stroke-width="4"/>'
    '<path d="M30,0 v30 M0,15 h60" stroke="#fff" stroke-width="10"/>'
    '<path d="M30,0 v30 M0,15 h60" stroke="#c8102e" stroke-width="6"/>'
    '</svg>'
)


def _flag_svg(code: str) -> str:
    if code == "en":
        return _UNION_JACK
    body = {
        "vi": f'<rect width="30" height="20" fill="#da251d"/><polygon fill="#ffff00" points="{_star(15, 10.6, 6)}"/>',
        "es": '<rect width="30" height="20" fill="#aa151b"/><rect y="5" width="30" height="10" fill="#f1bf00"/>',
        "zh": _china(),
        "ja": '<rect width="30" height="20" fill="#ffffff"/><circle cx="15" cy="10" r="6" fill="#bc002d"/>',
    }[code]
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="20" viewBox="0 0 30 20">{body}</svg>'


def flag_pixmap(code: str, height: int = 16) -> QPixmap:
    """Lá cờ tỉ lệ 3:2, bo góc nhẹ, viền mảnh (để cờ nền trắng như Nhật vẫn thấy rõ)."""
    width = round(height * 1.5)
    scale = 2  # vẽ gấp đôi cho nét trên màn hình độ phân giải cao
    pixmap = QPixmap(width * scale, height * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0.5, 0.5, width - 1, height - 1)
    clip = QPainterPath()
    clip.addRoundedRect(rect, 2, 2)
    painter.setClipPath(clip)
    QSvgRenderer(QByteArray(_flag_svg(code).encode("utf-8"))).render(painter, rect)
    painter.setClipping(False)
    painter.setPen(QColor(0, 0, 0, 60))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 2, 2)
    painter.end()
    return pixmap


def flag_icon(code: str) -> QIcon:
    return QIcon(flag_pixmap(code))


def apply_language(code: str) -> str:
    """Đổi ngôn ngữ của app: bảng dịch, QLocale mặc định và bản dịch hộp thoại có sẵn của Qt.

    Widget đã dựng rồi không tự đổi chữ – người gọi tự dựng lại (MainWindow.retranslate, Tray.retranslate).
    """
    code = i18n.set_language(code)
    QLocale.setDefault(QLocale(*_QT_LOCALES[code]))
    app = QCoreApplication.instance()
    if app is None:
        return code
    while _translators:
        app.removeTranslator(_translators.pop())
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    # Qt không có bản dịch tiếng Việt; tiếng Anh là ngôn ngữ gốc của Qt nên cũng không cần.
    if code not in ("vi", "en") and translator.load(QLocale(*_QT_LOCALES[code]), "qtbase", "_", folder):
        app.installTranslator(translator)
        _translators.append(translator)
    else:
        log.debug("Không nạp bản dịch Qt cho %s", code)
    return code
