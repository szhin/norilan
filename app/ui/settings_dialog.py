"""Cửa sổ Cài đặt: giao diện & màu sắc, màu môn học, nhắc việc, hệ thống."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PySide6.QtCore import QTime, QTimer, Qt, Signal
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
from app.colors import COLOR_BY, COLOR_BY_LABELS, ColorResolver
from app.settings import AUTO_DELETE_CHOICES, THEME_LABELS, THEMES, Settings
from app.ui import prompts
from app.ui.background import BackgroundError, import_background, load_background
from app.ui.color_picker import AccentPicker, ToneButton
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
        self.setWindowTitle(f"Cài đặt – {APP_NAME}")
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

        title = QLabel("Cài đặt", objectName="DetailTitle")
        root.addWidget(title)

        # ---- Giao diện
        root.addWidget(_section("Giao diện", "Chủ đề và màu nhấn đổi ngay để bạn xem trước."))
        form = self._form(root)
        self.theme = QComboBox()
        for key in THEMES:
            self.theme.addItem(THEME_LABELS[key], key)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        form.addRow("Chủ đề", self.theme)
        self.accent = AccentPicker(settings.accent, theme=settings.theme)
        form.addRow("Màu nhấn", self.accent)
        self.color_by = QComboBox()
        for key in COLOR_BY:
            self.color_by.addItem(COLOR_BY_LABELS[key], key)
        self.color_by.setCurrentIndex(self.color_by.findData(settings.color_by))
        form.addRow("Tô màu lịch theo", self.color_by)
        self.default_view = QComboBox()
        for mode in ViewMode:
            self.default_view.addItem(mode.label, mode.value)
        self.default_view.setCurrentIndex(self.default_view.findData(settings.default_view))
        form.addRow("Xem mặc định", self.default_view)
        self.theme.currentIndexChanged.connect(self._on_look_changed)
        self.accent.accent_changed.connect(lambda _: self._on_look_changed())

        # ---- Ảnh nền
        root.addWidget(_divider())
        root.addWidget(_section("Ảnh nền", "Ảnh được chép vào Norilan nên xoá hoặc di chuyển file gốc vẫn không sao."))
        bg_row = QHBoxLayout()
        bg_row.setSpacing(12)
        self.bg_preview = QLabel("Chưa có ảnh nền", objectName="BackgroundPreview")
        self.bg_preview.setFixedSize(176, 99)
        self.bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bg_row.addWidget(self.bg_preview)
        bg_controls = QVBoxLayout()
        bg_buttons = QHBoxLayout()
        choose = QPushButton("🖼  Chọn ảnh…")
        choose.clicked.connect(self._choose_background)
        self.bg_clear = QPushButton("Bỏ ảnh nền")
        self.bg_clear.clicked.connect(self._clear_background)
        bg_buttons.addWidget(choose)
        bg_buttons.addWidget(self.bg_clear)
        bg_buttons.addStretch(1)
        bg_controls.addLayout(bg_buttons)
        visibility_row = QHBoxLayout()
        visibility_row.addWidget(QLabel("Độ hiện rõ"))
        self.bg_visibility = QSlider(Qt.Orientation.Horizontal)
        self.bg_visibility.setRange(10, 90)
        self.bg_visibility.setValue(int(settings.background_visibility))
        self.bg_visibility.setToolTip("Càng cao ảnh càng rõ (các ô lịch càng trong suốt)")
        visibility_row.addWidget(self.bg_visibility, 1)
        bg_controls.addLayout(visibility_row)
        self.bg_blur = QCheckBox("Làm mờ ảnh (chữ dễ đọc hơn)")
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
        root.addWidget(_section("Màu môn học", "Mỗi môn có một màu trên lịch, giống thẻ màu trong Notion. "
                                                "Môn mới được tự gán màu; bấm để đổi."))
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        resolver = ColorResolver("subject", subject_colors)
        if not subjects:
            empty = QLabel("Chưa có môn học nào. Môn học sẽ xuất hiện ở đây khi bạn thêm bài tập.",
                           objectName="SettingsHint")
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
        root.addWidget(_section("Nhắc việc", "Thời gian nhắc trước cho từng bài tập / sự kiện đặt trong cửa sổ thêm–sửa."))
        digest_row = QHBoxLayout()
        self.digest_enabled = QCheckBox("Thông báo tổng hợp việc ngày mai lúc")
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
        root.addWidget(_section("Dọn dẹp", "Chỉ áp dụng cho bài tập đã xong; sự kiện không bị xoá."))
        purge_row = QHBoxLayout()
        purge_row.addWidget(QLabel("Tự xoá bài tập đã xong sau"))
        self.auto_delete = QComboBox()
        for days in AUTO_DELETE_CHOICES:
            self.auto_delete.addItem("Không bao giờ" if days == 0 else f"{days} ngày", days)
        index = self.auto_delete.findData(int(settings.auto_delete_done_days))
        if index < 0:  # giá trị tuỳ chỉnh trong settings.json
            self.auto_delete.addItem(f"{settings.auto_delete_done_days} ngày", int(settings.auto_delete_done_days))
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
        root.addWidget(_section("Dữ liệu", "Bản sao lưu là một file .zip gồm bài tập, sự kiện, cài đặt, "
                                           "bản sao file đính kèm và ảnh nền."))
        data_row = QHBoxLayout()
        for text, signal in (("💾  Sao lưu…", self.backup_requested), ("♻️  Khôi phục…", self.restore_requested),
                             ("📂  Mở thư mục dữ liệu", self.open_data_requested)):
            button = QPushButton(text)
            button.clicked.connect(signal)
            data_row.addWidget(button)
        data_row.addStretch(1)
        root.addLayout(data_row)

        # ---- Hệ thống
        root.addWidget(_divider())
        root.addWidget(_section("Hệ thống"))
        self.minimize_to_tray = QCheckBox("Đóng cửa sổ thì thu xuống khay hệ thống (vẫn chạy để nhắc việc)")
        self.minimize_to_tray.setChecked(settings.minimize_to_tray)
        self.autostart = QCheckBox("Khởi động cùng Windows (chạy ẩn ở khay)")
        self.autostart.setChecked(settings.autostart and autostart_supported)
        self.autostart.setEnabled(autostart_supported)
        if not autostart_supported:
            self.autostart.setToolTip("Chỉ có trên Windows.")
        self.copy_default = QCheckBox("Mặc định lưu bản sao file đính kèm vào Norilan")
        self.copy_default.setChecked(settings.copy_attachments_to_app_dir)
        for box in (self.minimize_to_tray, self.autostart, self.copy_default):
            root.addWidget(box)
        root.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 0, 24, 0)
        buttons.addStretch(1)
        cancel = QPushButton("Huỷ")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Lưu", objectName="PrimaryButton")
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
            self.bg_preview.setText("Chưa có ảnh nền")
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
        text = "⚠ Xoá vĩnh viễn, không hoàn tác được (trừ khi khôi phục từ bản sao lưu)."
        if count:
            text += f" Khi bấm Lưu, {count} bài tập đã xong hơn {days} ngày sẽ bị xoá ngay."
        self.purge_warning.setText(text)
        self.purge_warning.show()

    # --- kết quả
    def result_settings(self) -> Settings:
        digest = self.digest_time.time().toPython()
        return replace(
            self._original,
            theme=self.theme.currentData(),
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
