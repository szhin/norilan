"""Cửa sổ Cài đặt: giao diện & màu sắc, màu môn học, nhắc việc, hệ thống."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PySide6.QtCore import QSize, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.calendar_logic import ViewMode
from app.colors import COLOR_BY, ColorResolver, color_by_label
from app.i18n import LANGUAGE_NAMES, LANGUAGES, tr
from app.settings import AUTO_DELETE_CHOICES, THEMES, Settings, theme_label
from app.ui import prompts
from app.ui.background import BackgroundError, import_background, load_background
from app.ui.color_picker import AccentPicker, ToneButton
from app.ui.language import flag_icon
from app.ui.widgets import ElidedLabel


def _section(title: str, hint: str = "") -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(QLabel(title, objectName="SettingsSection"))
    if hint:
        label = QLabel(hint, objectName="SettingsHint")
        label.setWordWrap(True)
        layout.addWidget(label)
    return box


def _divider() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: palette(mid);")
    return line


class SettingsDialog(QDialog):
    """Sửa một bản sao của Settings; chỉ áp dụng khi bấm Lưu."""

    preview_changed = Signal(object)  # Settings đang chỉnh – để xem trước ngay
    backup_requested = Signal()
    restore_requested = Signal()
    open_data_requested = Signal()

    def __init__(
        self, settings: Settings, *, subjects: list[str], subject_colors: dict[str, str],
        autostart_supported: bool, purge_preview: Callable[[int], int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{tr('settings.title')} – {APP_NAME}")
        self.resize(580, 720)
        self._original = settings
        self._background = settings.background
        self.imported_backgrounds: list[str] = []
        self.restored = False  # đặt bởi controller khi khôi phục xong
        self._purge_preview = purge_preview or (lambda days: 0)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._emit_preview)
        self._subject_buttons: dict[str, ToneButton] = {}
        self._initial_tones: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 16, 24, 8)
        root.setSpacing(10)

        title = QLabel(tr("settings.title"), objectName="DetailTitle")
        root.addWidget(title)

        # ---- Giao diện
        root.addWidget(_section(tr("settings.appearance"), tr("settings.appearance_hint")))
        form = self._form(root)
        # Ngôn ngữ: lá cờ + tên viết bằng chính ngôn ngữ đó (ai cũng tìm được thứ tiếng của mình)
        self.language = QComboBox()
        self.language.setIconSize(QSize(24, 16))
        for code in LANGUAGES:
            self.language.addItem(flag_icon(code), LANGUAGE_NAMES[code], code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.language)))
        form.addRow(tr("settings.language"), self.language)
        self.theme = QComboBox()
        for key in THEMES:
            self.theme.addItem(theme_label(key), key)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        form.addRow(tr("settings.theme"), self.theme)
        self.accent = AccentPicker(settings.accent, theme=settings.theme)
        form.addRow(tr("settings.accent"), self.accent)
        self.color_by = QComboBox()
        for key in COLOR_BY:
            self.color_by.addItem(color_by_label(key), key)
        self.color_by.setCurrentIndex(self.color_by.findData(settings.color_by))
        form.addRow(tr("settings.color_by"), self.color_by)
        self.default_view = QComboBox()
        for mode in ViewMode:
            self.default_view.addItem(mode.label, mode.value)
        self.default_view.setCurrentIndex(self.default_view.findData(settings.default_view))
        form.addRow(tr("settings.default_view"), self.default_view)
        self.theme.currentIndexChanged.connect(self._on_look_changed)
        self.accent.accent_changed.connect(lambda _: self._on_look_changed())

        # ---- Ảnh nền
        root.addWidget(_divider())
        root.addWidget(_section(tr("settings.background"), tr("settings.background_hint")))
        bg_row = QHBoxLayout()
        bg_row.setSpacing(12)
        self.bg_preview = QLabel(tr("settings.bg_none"), objectName="BackgroundPreview")
        self.bg_preview.setFixedSize(176, 99)
        self.bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bg_row.addWidget(self.bg_preview)
        bg_controls = QVBoxLayout()
        bg_buttons = QHBoxLayout()
        choose = QPushButton(tr("settings.bg_choose"))
        choose.clicked.connect(self._choose_background)
        self.bg_clear = QPushButton(tr("settings.bg_clear"))
        self.bg_clear.clicked.connect(self._clear_background)
        bg_buttons.addWidget(choose)
        bg_buttons.addWidget(self.bg_clear)
        bg_buttons.addStretch(1)
        bg_controls.addLayout(bg_buttons)
        visibility_row = QHBoxLayout()
        visibility_row.addWidget(QLabel(tr("settings.bg_visibility")))
        self.bg_visibility = QSlider(Qt.Orientation.Horizontal)
        self.bg_visibility.setRange(10, 90)
        self.bg_visibility.setValue(int(settings.background_visibility))
        self.bg_visibility.setToolTip(tr("settings.bg_visibility_tooltip"))
        visibility_row.addWidget(self.bg_visibility, 1)
        bg_controls.addLayout(visibility_row)
        self.bg_blur = QCheckBox(tr("settings.bg_blur"))
        self.bg_blur.setChecked(settings.background_blur)
        bg_controls.addWidget(self.bg_blur)
        bg_controls.addStretch(1)
        bg_row.addLayout(bg_controls, 1)
        root.addLayout(bg_row)
        self.bg_visibility.valueChanged.connect(lambda _: self._preview_timer.start())
        self.bg_blur.toggled.connect(lambda _: self._on_background_changed())
        self._update_background_controls()

        # ---- Màu môn học
        root.addWidget(_divider())
        root.addWidget(_section(tr("settings.subject_colors"), tr("settings.subject_colors_hint")))
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        resolver = ColorResolver("subject", subject_colors)
        if not subjects:
            empty = QLabel(tr("settings.subjects_empty"), objectName="SettingsHint")
            empty.setWordWrap(True)
            grid.addWidget(empty, 0, 0)
        for row, subject in enumerate(sorted(subjects, key=str.casefold)):
            label = ElidedLabel(subject)
            button = ToneButton(resolver.subject_tone(subject))
            button.setMinimumWidth(130)
            grid.addWidget(label, row, 0)
            grid.addWidget(button, row, 1)
            self._subject_buttons[subject] = button
            self._initial_tones[subject] = button.tone()
        root.addWidget(grid_host)

        # ---- Nhắc việc
        root.addWidget(_divider())
        root.addWidget(_section(tr("settings.reminders"), tr("settings.reminders_hint")))
        digest_row = QHBoxLayout()
        self.digest_enabled = QCheckBox(tr("settings.digest"))
        self.digest_enabled.setChecked(settings.daily_digest_enabled)
        self.digest_time = QTimeEdit()
        self.digest_time.setDisplayFormat("HH:mm")
        self.digest_time.setTime(QTime(settings.digest_time.hour, settings.digest_time.minute))
        self.digest_time.setMinimumWidth(90)
        self.digest_enabled.toggled.connect(self.digest_time.setEnabled)
        self.digest_time.setEnabled(settings.daily_digest_enabled)
        digest_row.addWidget(self.digest_enabled)
        digest_row.addWidget(self.digest_time)
        digest_row.addStretch(1)
        root.addLayout(digest_row)

        # ---- Dọn dẹp
        root.addWidget(_divider())
        root.addWidget(_section(tr("settings.cleanup"), tr("settings.cleanup_hint")))
        purge_row = QHBoxLayout()
        purge_row.addWidget(QLabel(tr("settings.purge_label")))
        self.auto_delete = QComboBox()
        for days in AUTO_DELETE_CHOICES:
            label = tr("settings.purge_never") if days == 0 else tr("settings.purge_days", days=days, count=days)
            self.auto_delete.addItem(label, days)
        index = self.auto_delete.findData(int(settings.auto_delete_done_days))
        if index < 0:  # giá trị tuỳ chỉnh trong settings.json
            custom = int(settings.auto_delete_done_days)
            self.auto_delete.addItem(tr("settings.purge_days", days=custom, count=custom), custom)
            index = self.auto_delete.count() - 1
        self.auto_delete.setCurrentIndex(index)
        purge_row.addWidget(self.auto_delete)
        purge_row.addStretch(1)
        root.addLayout(purge_row)
        self.purge_warning = QLabel(objectName="ErrorLabel")
        self.purge_warning.setWordWrap(True)
        root.addWidget(self.purge_warning)
        self.auto_delete.currentIndexChanged.connect(lambda _: self._update_purge_warning())
        self._update_purge_warning()

        # ---- Dữ liệu
        root.addWidget(_divider())
        root.addWidget(_section(tr("settings.data"), tr("settings.data_hint")))
        data_row = QHBoxLayout()
        for text, signal in ((tr("settings.backup"), self.backup_requested),
                             (tr("settings.restore"), self.restore_requested),
                             (tr("settings.open_data"), self.open_data_requested)):
            button = QPushButton(text)
            button.clicked.connect(signal)
            data_row.addWidget(button)
        data_row.addStretch(1)
        root.addLayout(data_row)

        # ---- Hệ thống
        root.addWidget(_divider())
        root.addWidget(_section(tr("settings.system")))
        self.minimize_to_tray = QCheckBox(tr("settings.tray"))
        self.minimize_to_tray.setChecked(settings.minimize_to_tray)
        self.autostart = QCheckBox(tr("settings.autostart"))
        self.autostart.setChecked(settings.autostart and autostart_supported)
        self.autostart.setEnabled(autostart_supported)
        if not autostart_supported:
            self.autostart.setToolTip(tr("settings.autostart_tooltip"))
        self.copy_default = QCheckBox(tr("settings.copy_default"))
        self.copy_default.setChecked(settings.copy_attachments_to_app_dir)
        for box in (self.minimize_to_tray, self.autostart, self.copy_default):
            root.addWidget(box)
        root.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 0, 24, 0)
        buttons.addStretch(1)
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("common.save"), objectName="PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addLayout(buttons)

    def _form(self, parent_layout: QVBoxLayout) -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        parent_layout.addLayout(form)
        return form

    def _on_look_changed(self) -> None:
        self.accent.set_theme(self.theme.currentData())
        self._preview_timer.start()

    def _emit_preview(self) -> None:
        self.preview_changed.emit(self.result_settings())

    # --- ảnh nền
    def background(self) -> str:
        return self._background

    def _choose_background(self) -> None:
        path = prompts.pick_image(self)
        if not path:
            return
        try:
            name = import_background(path)
        except BackgroundError as exc:
            prompts.show_error(self, str(exc))
            return
        self.imported_backgrounds.append(name)
        self._background = name
        self._on_background_changed()

    def _clear_background(self) -> None:
        self._background = ""
        self._on_background_changed()

    def _on_background_changed(self) -> None:
        self._update_background_controls()
        self._preview_timer.start()

    def _update_background_controls(self) -> None:
        has = bool(self._background)
        self.bg_clear.setEnabled(has)
        self.bg_visibility.setEnabled(has)
        self.bg_blur.setEnabled(has)
        pixmap = load_background(self._background, blur=self.bg_blur.isChecked()) if has else None
        if pixmap is None:
            self.bg_preview.setPixmap(QPixmap())
            self.bg_preview.setText(tr("settings.bg_none"))
            return
        ratio = self.bg_preview.devicePixelRatioF()
        size = self.bg_preview.size() * ratio
        thumb = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
        thumb = thumb.copy((thumb.width() - size.width()) // 2, (thumb.height() - size.height()) // 2,
                           size.width(), size.height())
        thumb.setDevicePixelRatio(ratio)
        self.bg_preview.setPixmap(thumb)

    # --- tự xoá
    def _update_purge_warning(self) -> None:
        days = int(self.auto_delete.currentData())
        if days <= 0:
            self.purge_warning.hide()
            return
        count = self._purge_preview(days)
        text = tr("settings.purge_warning")
        if count:
            text += " " + tr("settings.purge_count", count=count, days=days)
        self.purge_warning.setText(text)
        self.purge_warning.show()

    # --- kết quả
    def result_settings(self) -> Settings:
        digest = self.digest_time.time().toPython()
        return replace(
            self._original,
            theme=self.theme.currentData(),
            language=self.language.currentData(),
            accent=self.accent.accent(),
            color_by=self.color_by.currentData(),
            default_view=self.default_view.currentData(),
            daily_digest_enabled=self.digest_enabled.isChecked(),
            daily_digest_time=f"{digest:%H:%M}",
            minimize_to_tray=self.minimize_to_tray.isChecked(),
            autostart=self.autostart.isChecked(),
            copy_attachments_to_app_dir=self.copy_default.isChecked(),
            background=self._background,
            background_visibility=self.bg_visibility.value(),
            background_blur=self.bg_blur.isChecked(),
            auto_delete_done_days=int(self.auto_delete.currentData()),
        )

    def subject_color_changes(self) -> dict[str, str]:
        """Các môn đã đổi màu so với lúc mở cửa sổ."""
        return {
            subject: button.tone()
            for subject, button in self._subject_buttons.items()
            if button.tone() != self._initial_tones[subject]
        }
