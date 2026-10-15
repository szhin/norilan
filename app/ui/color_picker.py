"""Chọn màu: nút chọn tone kiểu Notion (cho môn học / sự kiện, có cả gradient) và hàng ô màu nhấn của app."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.colors import (
    ACCENTS,
    GRADIENT_PRESETS,
    TONES,
    accent_color,
    accent_label,
    gradient_stops,
    is_gradient,
    is_hex,
    make_gradient,
    mix,
    readable_text_color,
    tone_label,
)
from app.i18n import tr
from app.ui.theme import gradient_qss, repolish, resolve_dark, tone_colors


def tone_icon(tone: str, size: int = 14) -> QIcon:
    """Ô vuông bo góc: nền + viền theo màu của tone (giống thẻ màu trong Notion); gradient thì tô chuyển màu."""
    pixmap = QPixmap(size * 2, size * 2)  # vẽ gấp đôi cho nét trên màn hình độ phân giải cao
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(1, 1, size - 2, size - 2)
    if is_gradient(tone):
        start, end = gradient_stops(tone)
        fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
        fill.setColorAt(0, QColor(start))
        fill.setColorAt(1, QColor(end))
        painter.setPen(QColor(mix(start, end, 0.5)).darker(130))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, 3.5, 3.5)
    else:
        bg, fg = tone_colors(tone)
        painter.setPen(QColor(fg))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 3.5, 3.5)
        painter.setBrush(QColor(fg))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(size / 2 - 2.5, size / 2 - 2.5, 5, 5))
    painter.end()
    return QIcon(pixmap)


class ToneButton(QPushButton):
    """Nút hiện màu đang chọn; bấm để mở bảng 9 màu + 'Tạo gradient…'."""

    tone_changed = Signal(str)

    def __init__(self, tone: str = "gray", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToneButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("tone.tooltip"))
        self._tone = ""
        self._menu = QMenu(self)
        for key in TONES:
            action = self._menu.addAction(tone_icon(key), tone_label(key))
            action.triggered.connect(lambda _=False, k=key: self.choose(k))
        self._menu.addSeparator()
        gradient = self._menu.addAction(tone_icon(make_gradient(*GRADIENT_PRESETS[0])), tr("tone.gradient_action"))
        gradient.triggered.connect(self._choose_gradient)
        self.setMenu(self._menu)
        self.set_tone(tone)

    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        """Đặt màu hiển thị (không phát tín hiệu)."""
        tone = tone if tone in TONES or is_gradient(tone) else "gray"
        self._tone = tone
        self.setIcon(tone_icon(tone))
        self.setText(tone_label(tone))

    def choose(self, tone: str) -> None:
        """Người dùng chọn màu (phát tone_changed)."""
        self.set_tone(tone)
        self.tone_changed.emit(self._tone)

    def _choose_gradient(self) -> None:
        from app.ui import prompts  # tránh vòng import

        start = self._tone if is_gradient(self._tone) else None
        if (value := prompts.pick_gradient(self, start)) is not None:
            self.choose(value)


def _swatch_style(start: str, end: str | None = None) -> str:
    """Nút ô màu: nền một màu hoặc gradient ngang, chữ đủ tương phản."""
    if end is None:
        fill, text = start, readable_text_color(start)
    else:
        fill = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {start}, stop:1 {end})"
        text = readable_text_color(start, end)
    return (f"QPushButton {{ background: {fill}; color: {text}; border: 2px solid rgba(0, 0, 0, 0.12); "
            f"border-radius: 6px; font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: palette(highlight); }}")


class GradientDialog(QDialog):
    """Tạo màu gradient: chọn màu đầu + màu cuối (hoặc bấm một mẫu gợi ý), xem trước ngay."""

    def __init__(self, start: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("gradient.title"))
        self.setMinimumWidth(380)
        first, last = gradient_stops(start) if is_gradient(start) else GRADIENT_PRESETS[0]
        self._colors = [first, last]

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        self.preview = QLabel(tr("gradient.preview"))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(52)
        root.addWidget(self.preview)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        self.stop_buttons: list[QPushButton] = []
        for index, (column, label) in enumerate(((0, "gradient.start"), (2, "gradient.end"))):
            grid.addWidget(QLabel(tr(label), objectName="Muted"), 0, column)
            button = QPushButton()
            button.setMinimumHeight(34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._pick(i))
            grid.addWidget(button, 1, column)
            self.stop_buttons.append(button)
        swap = QPushButton(tr("gradient.swap"))
        swap.clicked.connect(self.swap)
        grid.addWidget(swap, 1, 1)
        root.addLayout(grid)

        root.addWidget(QLabel(tr("gradient.presets"), objectName="Muted"))
        presets = QHBoxLayout()
        presets.setSpacing(6)
        self.preset_buttons: list[QPushButton] = []
        for a, b in GRADIENT_PRESETS:
            button = QPushButton()
            button.setFixedSize(42, 26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_swatch_style(a, b))
            button.clicked.connect(lambda _=False, a=a, b=b: self.set_colors(a, b))
            presets.addWidget(button)
            self.preset_buttons.append(button)
        presets.addStretch(1)
        root.addLayout(presets)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        apply = QPushButton(tr("gradient.apply"), objectName="PrimaryButton")
        apply.setDefault(True)
        apply.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        root.addLayout(buttons)
        self._refresh()

    def value(self) -> str:
        return make_gradient(*self._colors)

    def set_colors(self, start: str, end: str) -> None:
        if is_hex(start) and is_hex(end):
            self._colors = [start.lower(), end.lower()]
            self._refresh()

    def swap(self) -> None:
        self.set_colors(self._colors[1], self._colors[0])

    def _pick(self, index: int) -> None:
        from app.ui import prompts  # tránh vòng import

        title = tr("gradient.pick_start" if index == 0 else "gradient.pick_end")
        if (color := prompts.pick_color(self, self._colors[index], title)) is not None:
            colors = list(self._colors)
            colors[index] = color
            self.set_colors(*colors)

    def _refresh(self) -> None:
        self.preview.setStyleSheet(f"QLabel {{ {gradient_qss(self.value())} border-radius: 8px; "
                                   f"font-size: 14px; font-weight: 600; }}")
        for button, color in zip(self.stop_buttons, self._colors):
            button.setText(color.upper())
            button.setStyleSheet(_swatch_style(color))


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
                button.setToolTip(tr("accent.custom_tooltip"))
                button.clicked.connect(self._pick_custom)
            else:
                button.setToolTip(accent_label(key))
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
