"""Giao diện tối giản kiểu Notion. Màu khai báo dạng token để đổi light/dark chỉ bằng một dict."""
from __future__ import annotations

import tempfile
from pathlib import Path
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from app.colors import DARK_TONES, LIGHT_TONES, accent_tokens, gradient_stops, is_gradient, mix, readable_text_color

LIGHT: dict[str, str] = {
    "bg": "#ffffff", "text": "#37352f", "text_muted": "#787774", "text_faint": "#a5a29c",
    "border": "#ebebea", "sidebar_bg": "#f7f7f5", "hover": "#efefed", "selected": "#e6e6e4",
    "accent": "#2383e2", "accent_hover": "#1b6fc4", "accent_text": "#ffffff", "outside_bg": "#fbfbfa", "selected_cell": "#eef5fd",
    "task_bg": "#e3eefb", "task_fg": "#1d5a9c", "event_bg": "#fdecdc", "event_fg": "#94500f",
    "done_bg": "#f1f1ef", "done_fg": "#a5a29c", "overdue_bg": "#fde8e8", "overdue_fg": "#c4332b",
    "badge_bg": "#e7e6e3", "danger": "#d44c47", "warn_bg": "#fff0d1", "warn_fg": "#a85a00",
    "study_bg": "#e6f4ea", "study_fg": "#2f7a4a",
}

DARK: dict[str, str] = {
    "bg": "#191919", "text": "#e3e2e0", "text_muted": "#9b9a97", "text_faint": "#6f6e69",
    "border": "#2f2f2f", "sidebar_bg": "#202020", "hover": "#2c2c2c", "selected": "#373737",
    "accent": "#2f8fed", "accent_hover": "#4a9ff0", "accent_text": "#ffffff", "outside_bg": "#1e1e1e", "selected_cell": "#1d2a3a",
    "task_bg": "#1f3550", "task_fg": "#9cc7f5", "event_bg": "#46301c", "event_fg": "#f2bf8c",
    "done_bg": "#262626", "done_fg": "#6f6e69", "overdue_bg": "#4a2323", "overdue_fg": "#ff9b94",
    "badge_bg": "#333333", "danger": "#ff7b72", "warn_bg": "#4d3a14", "warn_fg": "#f3c05a",
    "study_bg": "#1f3d2b", "study_fg": "#7fd1a0",
}

THEMES = {"light": LIGHT, "dark": DARK}

_QSS = Template("""
* { font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px; color: $text; }
QMainWindow { background: $bg; }
#Central { background: transparent; }
QToolTip { background: $bg; color: $text; border: 1px solid $border; padding: 4px 6px; }

/* ---- Sidebar ---- */
#Sidebar { background: $surface_sidebar; border: none; border-right: 1px solid $border; }
QLineEdit#SearchBox { background: $surface_card; border: 1px solid $border; border-radius: 6px; padding: 5px 8px; }
QLineEdit#SearchBox:focus { border-color: $accent; }
#AppTitle { font-size: 16px; font-weight: 700; padding: 4px 8px 10px 8px; }
#SidebarFooter { color: $text_faint; font-size: 11px; padding: 4px 8px; }
#NavButton { background: transparent; border: none; border-radius: 6px; text-align: left; }
#NavButton:hover { background: $hover; }
#NavButton:checked { background: $selected; }
#NavText { background: transparent; }
#NavBadge { background: $badge_bg; color: $text_muted; border-radius: 8px; padding: 0px 6px; font-size: 11px; }
#NavBadge[danger="true"] { background: $overdue_bg; color: $overdue_fg; font-weight: 600; }
#AppLogo { background: transparent; }
QPushButton#SidebarAction { background: transparent; border: none; border-radius: 6px; text-align: left;
    padding: 6px 10px; color: $text_muted; }
QPushButton#SidebarAction:hover { background: $hover; color: $text; }
#SidebarSection { color: $text_faint; font-size: 11px; font-weight: 600; padding: 12px 10px 4px 10px; }

/* ---- Toolbar ---- */
#Toolbar { background: $surface_bar; border: none; border-bottom: 1px solid $border; }
#PeriodTitle { font-size: 20px; font-weight: 600; padding-left: 6px; }
QPushButton#ToolButton { background: $bg; border: 1px solid $border; border-radius: 6px; padding: 4px 12px; }
QPushButton#ToolButton:hover { background: $hover; }
QPushButton#ToolButton:pressed { background: $selected; }
QPushButton#SegButton { background: $bg; border: 1px solid $border; padding: 4px 14px; border-radius: 0px; min-width: 44px; }
QPushButton#SegButton[pos="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#SegButton[pos="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#SegButton:hover { background: $hover; }
QPushButton#SegButton:checked { background: $selected; font-weight: 600; }

/* ---- Lịch ---- */
#CalendarArea { background: transparent; }
#WeekdayHeader { background: $surface_bar; color: $text_muted; font-size: 11px; font-weight: 600; padding: 6px 8px;
                 border: none; border-bottom: 1px solid $border; }
#DayCell { background: $surface_cell; border: none; border-right: 1px solid $border; border-bottom: 1px solid $border; }
#DayCell[outside="true"] { background: $surface_outside; }
#DayCell:hover { background: $surface_hover; }
#DayCell[selected="true"] { background: $surface_selected; }
QPushButton#CellAdd { background: transparent; border: none; border-radius: 4px; padding: 0px;
    color: $text_muted; font-size: 15px; min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; }
QPushButton#CellAdd:hover { background: $hover; color: $text; }
#DayNumber { font-size: 12px; border-radius: 11px; }
#DayNumber[outside="true"] { color: $text_faint; }
#DayNumber[today="true"] { background: $accent; color: $accent_text; font-weight: 700; }
#DayWeekday { color: $text_muted; font-size: 11px; font-weight: 600; }
#DayWeekday[today="true"] { color: $accent; }
#Chip { border-radius: 4px; padding: 1px 6px; font-size: 12px; }
#Pill { border-radius: 4px; padding: 1px 6px; font-size: 12px; }
$tone_rules
#Chip[state="high"] { font-weight: 700; }
#Chip[state="overdue"] { background: $overdue_bg; color: $overdue_fg; }
#Chip[state="soon"] { background: $warn_bg; color: $warn_fg; font-weight: 700; }
#Chip[kind="deadline"] { font-weight: 600; }
#Chip[kind="deadline"][zero="true"] { border: 1px dashed $warn_fg; }
#Chip[state="done"] { background: $done_bg; color: $done_fg; }
#MoreLabel { color: $text_muted; font-size: 11px; font-weight: 600; padding: 0px 6px; }
#MoreLabel:hover { color: $accent; }

/* ---- Panel bên phải ---- */
#SidePanel { background: $surface_panel; border: none; border-left: 1px solid $border; }
#FilterRow QComboBox { padding: 3px 6px; }
#PanelTitle { font-size: 17px; font-weight: 600; }
#PanelSubtitle, #Muted { color: $text_muted; }
#GroupHeader { color: $text_muted; font-size: 11px; font-weight: 700; padding: 8px 2px 2px 2px; }
#ItemCard { background: $surface_card; border: 1px solid $border; border-radius: 8px; }
#ItemCard:hover { background: $outside_bg; border-color: $text_faint; }
#ItemCard[selected="true"] { border: 1px solid $accent; background: $selected_cell; }
#KindBar { border-radius: 2px; }
#KindBar[state="overdue"] { background: $overdue_fg; }
#KindBar[state="soon"] { background: $warn_fg; }
#KindBar[state="done"] { background: $done_fg; }
#CardTitle { font-weight: 600; }
#CardTitle[state="done"] { color: $done_fg; }
#CardMeta { color: $text_muted; font-size: 12px; }
#CardMeta[state="overdue"] { color: $overdue_fg; }
#CardMeta[state="soon"] { color: $warn_fg; font-weight: 600; }
#KindIcon { background: transparent; font-size: 13px; }
#StudyBadge { background: $badge_bg; color: $text_muted; border-radius: 8px; padding: 0px 6px; font-size: 11px; }
#StudyBadge[zero="true"] { background: $warn_bg; color: $warn_fg; font-weight: 700; }
#EmptyState { color: $text_faint; padding: 28px 8px; }

/* ---- Chi tiết ---- */
QPushButton#LinkButton { background: transparent; border: none; color: $text_muted; padding: 2px 0px; text-align: left; }
QPushButton#LinkButton:hover { color: $accent; }
#DetailTitle { font-size: 19px; font-weight: 700; }
#KindPill { border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600; }
#KindPill[kind="deadline"] { background: $task_bg; color: $task_fg; }
#KindPill[kind="event"] { background: $event_bg; color: $event_fg; }
#KindPill[kind="study"] { background: $study_bg; color: $study_fg; }
#FieldLabel { color: $text_muted; }
#FieldValue[state="overdue"] { color: $overdue_fg; font-weight: 600; }
#FieldValue[state="soon"] { color: $warn_fg; font-weight: 600; }
#StudyLink { background: $outside_bg; border: 1px solid $border; border-radius: 6px; text-align: left; padding: 4px 8px; }
#StudyLink:hover { border-color: $accent; }
#LinkedCard { background: $outside_bg; border: 1px solid $border; border-radius: 8px; }
#LinkedCard:hover { border-color: $accent; }
#StudyEmpty { color: $text_faint; }

/* ---- Đếm ngược Study time ---- */
#CountdownTimer { background: $outside_bg; border: 1px solid $border; border-radius: 8px; }
#CountdownTimer:hover { border-color: $accent; }
#CountdownText { font-size: 18px; font-weight: 700; background: transparent; }
#CountdownText[phase="upcoming"] { color: $text_muted; font-size: 14px; font-weight: 600; }
#CountdownText[phase="ended"] { color: $text_faint; font-size: 14px; font-weight: 600; }
#CountdownText[warn="true"] { color: $warn_fg; }
#OverlayCard { background: $bg; border: 1px solid $border; border-radius: 16px; }
#OverlayCaption { color: $text_muted; font-size: 15px; font-weight: 600; background: transparent; }
#OverlayDigits { font-size: 104px; font-weight: 700; background: transparent; }
#OverlayDigits[phase="upcoming"] { color: $text_muted; }
#OverlayDigits[phase="ended"] { color: $text_faint; font-size: 56px; }
#OverlayDigits[warn="true"] { color: $warn_fg; }
#OverlayTitle { font-size: 22px; font-weight: 700; background: transparent; }
#OverlayRange, #OverlayDeadline { color: $text_muted; font-size: 14px; background: transparent; }
QProgressBar#OverlayProgress { background: $border; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar#OverlayProgress::chunk { background: $accent; border-radius: 3px; }
QProgressBar#OverlayProgress[warn="true"]::chunk { background: $warn_fg; }
QPushButton#OverlayClose { border: none; background: transparent; font-size: 16px; padding: 2px 10px; color: $text_muted; }
QPushButton#OverlayClose:hover { background: $hover; color: $text; }
QPushButton#AddStudyButton { border-style: dashed; color: $text_muted; }
QPushButton#AddStudyButton:hover { color: $accent; border-color: $accent; }
#Attachment { background: $outside_bg; border: 1px solid $border; border-radius: 6px; }
#Attachment[missing="true"] { color: $danger; border-color: $danger; }

/* ---- Nút & ô nhập ---- */
QDialog { background: $bg; }
QPushButton { background: $bg; border: 1px solid $border; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $selected; }
QPushButton:disabled { color: $text_faint; }
QPushButton#PrimaryButton { background: $accent; color: $accent_text; border: 1px solid $accent; font-weight: 600; }
QPushButton#PrimaryButton:hover { background: $accent_hover; }
QPushButton#PrimaryButton:disabled { background: $border; border-color: $border; color: $text_faint; }
QPushButton#DangerButton { color: $danger; }
QPushButton#DangerButton:hover { background: $overdue_bg; }
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {
    background: $bg; border: 1px solid $border; border-radius: 6px; padding: 4px 6px;
    selection-background-color: $accent; selection-color: $accent_text; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus {
    border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: $text_faint; background: $outside_bg; }
QAbstractSpinBox { padding-right: 22px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; width: 20px; border: none; background: transparent; }
QAbstractSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: $hover; }
QAbstractSpinBox::up-arrow { image: url("$arrow_up"); width: 8px; height: 8px; }
QAbstractSpinBox::down-arrow { image: url("$arrow_down"); width: 8px; height: 8px; }
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::down-arrow:disabled { image: none; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down, QDateEdit::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }
QComboBox::down-arrow, QDateEdit::down-arrow { image: url("$arrow_down"); width: 10px; height: 10px; }
QComboBox::down-arrow:disabled { image: none; }
QLineEdit#TitleEdit { font-size: 17px; font-weight: 600; padding: 6px 8px; }
QComboBox QAbstractItemView { background: $bg; border: 1px solid $border; selection-background-color: $hover;
    selection-color: $text; outline: none; }
QCheckBox { spacing: 8px; }
QCheckBox:disabled { color: $text_faint; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $text_faint; border-radius: 4px; background: $bg; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url("$icon_check"); }
QCheckBox::indicator:disabled { background: $outside_bg; border-color: $border; }
QCheckBox::indicator:checked:disabled { background: $text_faint; border-color: $text_faint; }
QListWidget#AttachmentList { background: $bg; border: 1px solid $border; border-radius: 6px; outline: none; }
QListWidget#AttachmentList::item { padding: 6px 6px; border-radius: 4px; }
QListWidget#AttachmentList::item:hover { background: $hover; }
QListWidget#AttachmentList::item:selected { background: $selected; color: $text; }
#DropHint { color: $text_faint; font-size: 12px; }
#ErrorLabel { color: $danger; }
#SectionLabel { color: $text_muted; font-size: 11px; font-weight: 700; }
QMenu { background: $bg; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: $hover; color: $text; }
QMenu::item:disabled { color: $text_faint; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }

/* ---- Cài đặt & chọn màu ---- */
#SettingsSection { font-size: 14px; font-weight: 700; padding-top: 6px; }
#SettingsHint { color: $text_muted; font-size: 12px; }
QPushButton#AccentSwatch { border: 2px solid transparent; border-radius: 13px; padding: 0px;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }
QPushButton#AccentSwatch:hover { border-color: $border; }
QPushButton#AccentSwatch[selected="true"] { border-color: $text; }
QPushButton#ToneButton { text-align: left; padding: 4px 10px; }
#FieldIcon { color: $text_faint; }

/* ---- Đính kèm trong chi tiết ---- */
QPushButton#AttachmentOpen { background: transparent; border: none; text-align: left; padding: 2px 0px; color: $text; }
QPushButton#AttachmentOpen:hover { color: $accent; text-decoration: underline; }
QPushButton#AttachmentOpen[missing="true"] { color: $danger; }
QPushButton#IconButton { background: transparent; border: none; padding: 2px 4px; border-radius: 4px; }
QPushButton#IconButton:hover { background: $hover; }

/* ---- Cuộn ---- */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#qt_scrollarea_viewport { background: transparent; }
#BackgroundPreview { border: 1px solid $border; border-radius: 6px; background: $outside_bg; color: $text_faint; }
QSlider::groove:horizontal { height: 4px; background: $border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $bg; border: 1px solid $text_faint; width: 14px; height: 14px;
    margin: -6px 0px; border-radius: 8px; }
QSlider::handle:horizontal:hover { border-color: $accent; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $text_faint; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSplitter::handle { background: $border; }
""")


# (tên trong QSS, nét vẽ SVG 10x10, token màu)
_ICONS = (
    ("arrow_up", "M2 6.5 L5 3.5 L8 6.5", "text_muted"),
    ("arrow_down", "M2 3.5 L5 6.5 L8 3.5", "text_muted"),
    ("icon_check", "M2.2 5.2 L4.2 7.2 L7.8 3.2", "accent_text"),
)


def _icons(tokens: dict[str, str]) -> dict[str, str]:
    """Ghi icon SVG theo màu theme ra thư mục tạm; trả về {tên: đường dẫn} dùng trong QSS."""
    folder = Path(tempfile.gettempdir()) / "norilan-ui"
    folder.mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}
    for name, path, color_key in _ICONS:
        color = tokens[color_key]
        file = folder / f"{name}-{color.lstrip('#')}.svg"
        if not file.exists():
            file.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>',
                encoding="utf-8",
            )
        result[name] = file.as_posix()
    return result


_current: dict[str, object] = {"dark": False, "tokens": LIGHT}


def system_is_dark() -> bool:
    """Windows đang ở chế độ tối? (Qt ≥ 6.5; không xác định được thì coi là sáng.)"""
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def is_dark() -> bool:
    """Theme đang áp dụng là tối? (sau apply_theme)"""
    return bool(_current["dark"])


def resolve_dark(theme: str) -> bool:
    return system_is_dark() if theme == "system" else theme == "dark"


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {round(max(0.0, min(1.0, alpha)) * 255)})"


def _surfaces(tokens: dict[str, str], visibility: int | None) -> dict[str, str]:
    """Màu các mặt nền. Có ảnh nền → trong suốt theo mức 'hiện rõ': ô lịch trong nhất, thẻ gần như đục
    (để chữ luôn dễ đọc)."""
    v = 0.0 if visibility is None else max(10, min(90, visibility)) / 100
    cell, panel, card = 1 - 0.85 * v, 1 - 0.5 * v, 1 - 0.2 * v
    return {
        "surface_cell": _rgba(tokens["bg"], cell),
        "surface_outside": _rgba(tokens["outside_bg"], cell),
        "surface_hover": _rgba(tokens["hover_cell"], max(cell, 0.6) if visibility else 1),
        "surface_selected": _rgba(tokens["selected_cell"], max(cell, 0.8) if visibility else 1),
        "surface_sidebar": _rgba(tokens["sidebar_bg"], panel),
        "surface_bar": _rgba(tokens["bg"], panel),
        "surface_panel": _rgba(tokens["bg"], panel),
        "surface_card": _rgba(tokens["bg"], card),
    }


def build_tokens(theme: str, accent: str = "blue", background_visibility: int | None = None) -> dict[str, str]:
    dark = resolve_dark(theme)
    base = dict(DARK if dark else LIGHT)
    base.update(accent_tokens(accent, dark=dark, bg=base["bg"]))
    base["hover_cell"] = mix(base["bg"], base["text"], 0.035)
    base.update(_surfaces(base, background_visibility))
    return base


def tone_colors(tone: str) -> tuple[str, str]:
    """(nền, chữ) của một tone theo theme đang dùng – để vẽ ô màu trong bảng chọn."""
    table = DARK_TONES if _current["dark"] else LIGHT_TONES
    return table.get(tone, table["gray"])


def current_tokens() -> dict[str, str]:
    return dict(_current["tokens"])  # type: ignore[arg-type]


def _tone_rules(dark: bool) -> str:
    rules = []
    for tone, (bg, fg) in (DARK_TONES if dark else LIGHT_TONES).items():
        rules.append(f'#Chip[tone="{tone}"], #Pill[tone="{tone}"] {{ background: {bg}; color: {fg}; }}')
        rules.append(f'#KindBar[tone="{tone}"] {{ background: {fg}; }}')
        rules.append(f'#Chip[kind="deadline"][tone="{tone}"] {{ border: 1px solid {fg}; }}')  # viền riêng của Deadline
    return "\n".join(rules)


def stylesheet(theme: str, accent: str = "blue", background_visibility: int | None = None) -> str:
    tokens = build_tokens(theme, accent, background_visibility)
    return _QSS.substitute({**tokens, **_icons(tokens), "tone_rules": _tone_rules(resolve_dark(theme))})


def palette(theme: str, accent: str = "blue") -> QPalette:
    """Bảng màu khớp theme – để Windows ở chế độ tối không 'lấn' vào phần QSS không phủ hết
    (ô tích, lịch chọn ngày, hộp thoại hệ thống...)."""
    tokens = build_tokens(theme, accent)
    role = QPalette.ColorRole
    pal = QPalette()
    for color_role, key in (
        (role.Window, "bg"), (role.WindowText, "text"), (role.Base, "bg"), (role.AlternateBase, "outside_bg"),
        (role.Text, "text"), (role.Button, "bg"), (role.ButtonText, "text"), (role.BrightText, "danger"),
        (role.Highlight, "accent"), (role.HighlightedText, "accent_text"), (role.ToolTipBase, "bg"),
        (role.ToolTipText, "text"), (role.PlaceholderText, "text_faint"), (role.Link, "accent"),
        (role.Light, "bg"), (role.Midlight, "outside_bg"), (role.Mid, "border"), (role.Dark, "text_faint"),
    ):
        pal.setColor(color_role, QColor(tokens[key]))
    for color_role in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, color_role, QColor(tokens["text_faint"]))
    return pal


def apply_theme(
    app: QApplication, theme: str, accent: str = "blue", background_visibility: int | None = None
) -> None:
    """background_visibility: None = không có ảnh nền; 10–90 = có ảnh nền, các mặt nền trong suốt tương ứng."""
    _current["dark"] = resolve_dark(theme)
    _current["tokens"] = build_tokens(theme, accent, background_visibility)
    app.setPalette(palette(theme, accent))
    app.setStyleSheet(stylesheet(theme, accent, background_visibility))


# Trạng thái có màu riêng trong QSS chung (đè lên tone): chip/thanh của việc đã xong hoặc quá hạn.
STATE_OWNS_COLOR = ("done", "overdue", "soon")


def gradient_qss(tone: str, *, vertical: bool = False) -> str:
    """QSS của một tone gradient: nền chuyển màu (ngang; dọc cho thanh màu bên thẻ) + chữ đủ tương phản."""
    start, end = gradient_stops(tone)
    x2, y2 = (0, 1) if vertical else (1, 0)
    return (f"background: qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, stop:0 {start}, stop:1 {end}); "
            f"color: {readable_text_color(start, end)};")


def apply_tone(widget: QWidget, tone: str, *, vertical: bool = False, active: bool = True) -> None:
    """Gán tone cho widget (chip, thẻ màu, thanh màu).

    Tone thường chỉ cần dynamic property 'tone' – stylesheet chung của app tô màu. Gradient có vô số giá trị
    nên không có luật dựng sẵn: gắn stylesheet riêng lên widget. `active=False` (xem STATE_OWNS_COLOR) thì
    bỏ stylesheet riêng để màu theo trạng thái trong stylesheet chung được áp dụng.
    """
    sheet = gradient_qss(tone, vertical=vertical) if active and is_gradient(tone) else ""
    changed = widget.property("tone") != tone
    widget.setProperty("tone", tone)
    if widget.styleSheet() != sheet:
        widget.setStyleSheet(sheet)
        changed = True
    if changed:
        repolish(widget)


def repolish(widget: QWidget) -> None:
    """Áp lại QSS sau khi đổi dynamic property (vd. selected, state)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
