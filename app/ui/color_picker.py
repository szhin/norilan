"""Chọn màu: nút chọn tone kiểu Notion (cho môn học / sự kiện) và hàng ô màu nhấn của app."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QWidget

from app.colors import ACCENT_LABELS, ACCENTS, TONE_LABELS, TONES, accent_color, is_hex
from app.ui.theme import repolish, resolve_dark, tone_colors


def tone_icon(tone: str, size: int = 14) -> QIcon:
    """Ô vuông bo góc: nền + viền theo màu của tone (giống thẻ màu trong Notion)."""
    bg, fg = tone_colors(tone)
    pixmap = QPixmap(size * 2, size * 2)  # vẽ gấp đôi cho nét trên màn hình độ phân giải cao
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(fg))
    painter.setBrush(QColor(bg))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 3.5, 3.5)
    painter.setBrush(QColor(fg))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(size / 2 - 2.5, size / 2 - 2.5, 5, 5))
    painter.end()
    return QIcon(pixmap)


class ToneButton(QPushButton):
    """Nút hiện màu đang chọn; bấm để mở bảng 9 màu."""

    tone_changed = Signal(str)

    def __init__(self, tone: str = "gray", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToneButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Đổi màu")
        self._tone = ""
        self._menu = QMenu(self)
        for key in TONES:
            action = self._menu.addAction(tone_icon(key), TONE_LABELS[key])
            action.triggered.connect(lambda _=False, k=key: self.choose(k))
        self.setMenu(self._menu)
        self.set_tone(tone)

    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        """Đặt màu hiển thị (không phát tín hiệu)."""
        tone = tone if tone in TONES else "gray"
        self._tone = tone
        self.setIcon(tone_icon(tone))
        self.setText(TONE_LABELS[tone])

    def choose(self, tone: str) -> None:
        """Người dùng chọn màu (phát tone_changed)."""
        self.set_tone(tone)
        self.tone_changed.emit(self._tone)


class AccentPicker(QWidget):
    """Hàng ô tròn chọn màu nhấn + nút 'Tuỳ chỉnh…' (mã màu bất kỳ)."""

    accent_changed = Signal(str)

    def __init__(self, accent: str = "blue", *, theme: str = "light", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent
        self._theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._swatches: dict[str, QPushButton] = {}
        for key in [*ACCENTS, "custom"]:
            button = QPushButton(objectName="AccentSwatch")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if key == "custom":
                button.setToolTip("Tuỳ chỉnh màu…")
                button.clicked.connect(self._pick_custom)
            else:
                button.setToolTip(ACCENT_LABELS[key])
                button.clicked.connect(lambda _=False, k=key: self.set_accent(k, emit=True))
            layout.addWidget(button)
            self._swatches[key] = button
        layout.addStretch(1)
        self._paint()

    def accent(self) -> str:
        return self._accent

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._paint()

    def set_accent(self, accent: str, *, emit: bool = False) -> None:
        if accent not in ACCENTS and not is_hex(accent):
            return
        self._accent = accent
        self._paint()
        if emit:
            self.accent_changed.emit(accent)

    def _pick_custom(self) -> None:
        from app.ui import prompts  # tránh vòng import

        start = accent_color(self._accent, dark=resolve_dark(self._theme))
        if (color := prompts.pick_color(self, start)) is not None:
            self.set_accent(color, emit=True)

    def _paint(self) -> None:
        dark = resolve_dark(self._theme)
        custom = is_hex(self._accent)
        for key, button in self._swatches.items():
            if key == "custom":
                fill = self._accent if custom else "qconicalgradient(cx:0.5, cy:0.5, angle:0, stop:0 #e5484d, " \
                    "stop:0.33 #e2b93b, stop:0.66 #30a46c, stop:1 #3e63dd)"
                selected = custom
            else:
                fill = accent_color(key, dark=dark)
                selected = key == self._accent
            button.setStyleSheet(f"background: {fill};")
            if button.property("selected") != selected:
                button.setProperty("selected", selected)
                repolish(button)
