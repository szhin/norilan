"""Đa ngôn ngữ (5 thứ tiếng) và màu gradient cho môn học / sự kiện."""
from __future__ import annotations

import re
import string
from datetime import date, datetime
from pathlib import Path

import pytest

from app import i18n
from app.calendar_logic import REMIND_UNITS, ViewMode, day_heading, format_minutes, period_title
from app.colors import (
    GRADIENT_PRESETS,
    TONES,
    ColorResolver,
    is_gradient,
    is_valid_tone,
    make_gradient,
    readable_text_color,
    tone_label,
)
from app.i18n import LANGUAGES, tr
from app.models import Event, Priority, Task, TaskStatus, ValidationError
from app.settings import Settings, load_settings, save_settings

APP_DIR = Path(__file__).resolve().parent.parent / "app"
SUNSET = make_gradient("#FF9A5A", "#e0457b")


# ------------------------------------------------------------------ Bảng dịch
# Tham số code luôn truyền nhưng mỗi ngôn ngữ dùng tuỳ ý (vi: 'Tháng 10', en: 'October')
OPTIONAL_PARAMS = {"fmt.month_year": {"month", "month_name"}}


def _fields(text: str) -> set[str]:
    return {name.split(":")[0] for _, name, _, _ in string.Formatter().parse(text) if name}


def _forms(value: object) -> list[str]:
    return list(value.values()) if isinstance(value, dict) else [value]  # type: ignore[list-item]


@pytest.mark.parametrize("code", [c for c in LANGUAGES if c != "vi"])
def test_every_catalog_matches_vietnamese(code):
    source, catalog = i18n._CATALOGS["vi"], i18n._CATALOGS[code]
    assert set(catalog) == set(source), "thiếu/thừa khoá so với vi.py"
    for key, original in source.items():
        value = catalog[key]
        if isinstance(original, tuple):
            assert isinstance(value, tuple) and len(value) == len(original), key
            continue
        if isinstance(value, dict):
            assert set(value) == {"one", "other"}, key
        expected = _fields(original)  # type: ignore[arg-type]
        optional = OPTIONAL_PARAMS.get(key, set())
        for form in _forms(value):
            assert isinstance(form, str), key
            assert _fields(form) <= expected | optional | {"count"}, f"{code}:{key} có tham số lạ"
        assert _fields(_forms(value)[-1]) | optional | {"count"} >= expected - {"count"}, f"{code}:{key} thiếu tham số"


def test_every_key_used_in_code_exists():
    pattern = re.compile(r"\btr(?:_seq)?\(\s*([\"'])([\w.]+)\1")
    used = {m.group(2) for path in APP_DIR.rglob("*.py") for m in pattern.finditer(path.read_text(encoding="utf-8"))}
    dynamic = (
        [f"tone.{t}" for t in TONES] + [f"status.{s.value}" for s in TaskStatus]
        + [f"priority.{p.name.lower()}" for p in Priority] + [f"view.{v.value}" for v in ViewMode]
        + [f"remind.unit.{key}" for key, _ in REMIND_UNITS]
        + [f"nav.{k}" for k in ("calendar", "today", "tomorrow", "upcoming", "overdue")]
        + [f"field.{k}" for k in ("due", "status", "priority", "subject", "remind", "notes", "attachments",
                                   "time", "location")]
        + [f"{p}_{w}" for p in ("missing.title", "missing.text", "missing.detail") for w in ("file", "folder")]
        + [f"delete.{w}_{s}" for w in ("task", "event") for s in ("title", "text")]
        + [f"theme.{t}" for t in ("light", "dark", "system")] + [f"color_by.{k}" for k in ("subject", "type")]
    )
    missing = sorted(k for k in used | set(dynamic) if k not in i18n._CATALOGS["vi"])
    assert not missing


def test_tr_plural_fallback_and_unknown_language():
    assert i18n.set_language("fr") == "vi" and tr("nav.today") == "Hôm nay"
    i18n.set_language("en")
    assert tr("day.subtitle", count=1) == "1 item" and tr("day.subtitle", count=3) == "3 items"
    assert tr("khong.ton.tai") == "khong.ton.tai"


@pytest.mark.parametrize("code, heading, month, minutes", [
    ("vi", "Thứ Tư, 14/10/2026", "Tháng 10, 2026", "1 ngày 1 giờ 30 phút"),
    ("en", "Wednesday, 14/10/2026", "October 2026", "1 day 1 hour 30 minutes"),
    ("es", "Miércoles, 14/10/2026", "Octubre de 2026", "1 día 1 hora 30 minutos"),
    ("zh", "2026/10/14 星期三", "2026年10月", "1天1小时30分钟"),
    ("ja", "2026/10/14 水曜日", "2026年10月", "1日1時間30分"),
])
def test_calendar_labels_per_language(code, heading, month, minutes):
    i18n.set_language(code)
    day = date(2026, 10, 14)
    assert day_heading(day) == heading
    assert period_title(ViewMode.MONTH, day) == month
    assert format_minutes(1530) == minutes
    assert TaskStatus.DONE.label == tr("status.done")


def test_validation_messages_follow_language():
    i18n.set_language("en")
    with pytest.raises(ValidationError, match="title cannot be empty"):
        Task(" ", datetime(2026, 10, 14)).validate()


def test_language_setting_saved_and_validated(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(Settings(language="ja"), path)
    assert load_settings(path).language == "ja"
    assert Settings().language == "vi"
    with pytest.raises(ValueError):
        Settings(language="fr").validate()


# ------------------------------------------------------------------ Gradient (không Qt)
def test_gradient_values():
    assert SUNSET == "gradient:#ff9a5a,#e0457b" and is_gradient(SUNSET) and is_valid_tone(SUNSET)
    assert not is_gradient("gradient:#ff9a5a") and not is_gradient("blue") and not is_gradient(None)
    with pytest.raises(ValueError):
        make_gradient("red", "#000000")
    assert tone_label(SUNSET) == "Gradient" and tone_label("red") == "Đỏ"
    assert all(is_gradient(make_gradient(a, b)) for a, b in GRADIENT_PRESETS)


def test_readable_text_color_picks_contrast():
    assert readable_text_color("#000000", "#1a237e") == "#ffffff"
    assert readable_text_color("#fff9c4", "#ffffff") == "#1f1f1f"


def test_gradient_for_subjects_and_events():
    due = datetime(2026, 10, 15, 9)
    resolver = ColorResolver("subject", {"Toán": SUNSET})
    assert resolver.tone(Task("A", due, subject="Toán")) == SUNSET
    event = Event("Họp", due, due, color=SUNSET)
    event.validate()  # màu gradient hợp lệ cho sự kiện
    assert resolver.tone(event) == SUNSET
    with pytest.raises(ValidationError):
        Event("Họp", due, due, color="gradient:#zzzzzz,#000000").validate()


# ------------------------------------------------------------------ Giao diện
qt = pytest.importorskip("PySide6")


def test_flags_render_for_every_language(qapp):
    from app.ui.language import flag_pixmap

    for code in LANGUAGES:
        pixmap = flag_pixmap(code)
        assert not pixmap.isNull() and pixmap.deviceIndependentSize().width() == 24


def test_tone_button_creates_gradient(qapp, prompts_stub):
    from app.ui.color_picker import ToneButton

    button = ToneButton("blue")
    changed = []
    button.tone_changed.connect(changed.append)
    gradient_action = button.menu().actions()[-1]
    prompts_stub.gradient = None            # bấm Huỷ trong hộp thoại → giữ màu cũ
    gradient_action.trigger()
    assert button.tone() == "blue" and changed == []
    prompts_stub.gradient = SUNSET
    gradient_action.trigger()
    assert button.tone() == SUNSET and changed == [SUNSET] and button.text() == "Gradient"
    gradient_action.trigger()               # sửa tiếp: hộp thoại mở với gradient đang dùng
    assert prompts_stub.gradient_starts == [None, None, SUNSET]


def test_gradient_dialog(qapp, prompts_stub):
    from app.ui.color_picker import GradientDialog

    dialog = GradientDialog(SUNSET)
    assert dialog.value() == SUNSET
    dialog.swap()
    assert dialog.value() == "gradient:#e0457b,#ff9a5a"
    dialog.preset_buttons[1].click()
    assert dialog.value() == make_gradient(*GRADIENT_PRESETS[1])
    prompts_stub.color = "#123456"
    dialog.stop_buttons[1].click()
    assert dialog.value() == make_gradient(GRADIENT_PRESETS[1][0], "#123456")
    assert "qlineargradient" in dialog.preview.styleSheet()
    assert GradientDialog(None).value() == make_gradient(*GRADIENT_PRESETS[0])


def test_chip_paints_gradient_but_status_color_wins(qapp):
    from app.ui.widgets import Chip

    chip = Chip()
    task = Task("Lab", datetime(2026, 10, 15, 9), id=1)
    chip.set_item(task, task.due_at.date(), datetime(2026, 10, 14, 9), SUNSET)
    assert "qlineargradient" in chip.styleSheet() and chip.property("tone") == SUNSET
    chip.set_item(task, task.due_at.date(), datetime(2026, 10, 16, 9), SUNSET)  # đã quá hạn
    assert chip.styleSheet() == ""
    chip.set_item(task, task.due_at.date(), datetime(2026, 10, 14, 9), "green")
    assert chip.styleSheet() == "" and chip.property("tone") == "green"


@pytest.fixture
def controller(qapp, repo, launcher, prompts_stub):
    from app import paths
    from app.files import FileService
    from app.notifier import RecordingNotifier
    from app.ui.app_controller import AppController

    c = AppController(qapp, repo, Settings(), settings_path=paths.settings_path(), files=FileService(launcher),
                      notifier=RecordingNotifier(), clock=lambda: datetime(2026, 10, 14, 10), use_tray=False)
    yield c
    c.shutdown()
    c.window.quit_app()


def test_switch_language_in_settings_rebuilds_ui(controller, repo):
    task = repo.add_task(Task("Lab 2", datetime(2026, 10, 14, 23, 59), subject="Toán"))
    controller.window.open_item(task.id)
    assert controller.window.period_label.text() == "Tháng 10, 2026"

    def choose_english(dialog):
        assert [dialog.language.itemText(i) for i in range(dialog.language.count())] == [
            "Tiếng Việt", "English", "Español", "中文", "日本語"]
        assert not dialog.language.itemIcon(1).isNull()  # có lá cờ
        dialog.language.setCurrentIndex(dialog.language.findData("en"))
        dialog._subject_buttons["Toán"].choose(SUNSET)
        return True

    controller._exec_dialog = choose_english
    controller.open_settings()
    w = controller.window
    assert controller.settings.language == "en" and i18n.current_language() == "en"
    assert w.period_label.text() == "October 2026"
    assert w.panel.detail_item_id() == task.id                 # vẫn mở đúng mục như trước khi đổi
    assert w.panel.detail.field_values()["Due"] == "23:59 · Wednesday, 14/10/2026"
    assert w.central.findChild(type(w.sidebar)) is w.sidebar   # sidebar mới nằm trong cửa sổ
    assert repo.subject_colors()["Toán"] == SUNSET
    assert w.month_view.cell_for(date(2026, 10, 14)).chip_tones() == [SUNSET]
    w.go_today()  # phím tắt/nút vẫn chạy trên widget mới
    w.panel.show_list_page()
    assert w.panel.title() == "Wednesday, 14/10/2026"
