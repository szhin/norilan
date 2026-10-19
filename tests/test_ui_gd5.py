"""GĐ5: tìm kiếm, ảnh nền, tự xoá việc đã xong, sao lưu/khôi phục, đóng gói (offscreen)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QColor, QImage, QImageReader

from app import paths
from app.files import FileService
from app.models import Event, Deadline, DeadlineStatus
from app.notifier import RecordingNotifier
from app.settings import Settings, load_settings
from app.ui import theme
from app.ui.app_controller import AppController
from app.ui.background import BackgroundError, import_background

pytestmark = pytest.mark.usefixtures("prompts_stub")
NOW = datetime(2026, 10, 14, 10, 0)
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def make_controller(qapp, repo, launcher):
    created = []

    def make(settings: Settings | None = None):
        controller = AppController(qapp, repo, settings or Settings(), settings_path=paths.settings_path(),
                                   files=FileService(launcher), notifier=RecordingNotifier(),
                                   clock=lambda: NOW, use_tray=False)
        created.append(controller)
        return controller

    yield make
    for controller in created:
        controller.shutdown()
        controller.window.quit_app()
    theme.apply_theme(qapp, "light")


def make_image(path: Path, width: int = 800, height: int = 450) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#6a8dff"))
    image.save(str(path))
    return path


# ------------------------------------------------------------------ Tìm kiếm
@pytest.fixture
def searchable(repo):
    repo.add_deadline(Deadline("Ôn Giải tích", datetime(2026, 10, 15, 9), subject="Toán"))
    done = repo.add_deadline(Deadline("Bài Giải tích cũ", datetime(2026, 10, 1, 9), subject="Toán"))
    repo.set_deadline_status(done.id, DeadlineStatus.DONE)
    repo.add_event(Event("Họp CLB", datetime(2026, 10, 16, 18), datetime(2026, 10, 16, 19), location="Nhà văn hoá"))


def test_search_from_sidebar(make_controller, searchable):
    w = make_controller().window
    w.search("giai tich")                                     # không dấu vẫn ra
    assert w.panel.title() == "Tìm kiếm" and w.panel.filter_row.isVisibleTo(w.panel)
    assert w.panel.list.titles() == ["Bài Giải tích cũ", "Ôn Giải tích"]
    assert "2 kết quả" in w.panel.subtitle()
    assert not any(b.isChecked() for b in w.sidebar.buttons.values())

    w.panel.status_filter.setCurrentIndex(w.panel.status_filter.findData(DeadlineStatus.DONE))
    assert w.panel.list.titles() == ["Bài Giải tích cũ"]
    w.search("van hoa")
    assert w.panel.list.titles() == []                      # bộ lọc 'Xong' vẫn đang bật
    w.panel.status_filter.setCurrentIndex(0)
    assert w.panel.list.titles() == ["Họp CLB"]


def test_search_debounce_and_leave(make_controller, searchable):
    w = make_controller().window
    w.sidebar.search.setText("giai")                          # gõ trong ô → chờ ngừng gõ mới tìm
    assert w.panel.title() != "Tìm kiếm"
    w._search_timer.timeout.emit()
    assert w.panel.title() == "Tìm kiếm"
    w.on_nav("calendar")                                     # bấm mục khác → thoát tìm kiếm
    assert w.sidebar.search.text() == "" and not w.panel.filter_row.isVisibleTo(w.panel)
    w.search("")
    assert w.panel.title() != "Tìm kiếm"


def test_open_search_result_keeps_results(make_controller, searchable, repo):
    w = make_controller().window
    w.search("ôn")
    item_id = repo.search("Ôn Giải tích")[0].id
    w.activate_item(item_id)
    assert w.panel.detail_item_id() == item_id and w.selected_day == date(2026, 10, 15)
    w.panel.show_list_page()
    assert w.panel.title() == "Tìm kiếm"


# ------------------------------------------------------------------ Ảnh nền
def test_import_background_downscales_and_rejects_bad_files(tmp_path):
    big = make_image(tmp_path / "big.png", 4000, 2000)
    name = import_background(big)
    stored = QImage(str(paths.backgrounds_dir() / name))
    assert max(stored.width(), stored.height()) == 2560
    bad = tmp_path / "bad.png"
    bad.write_text("không phải ảnh")
    with pytest.raises(BackgroundError):
        import_background(bad)


def test_background_applied_and_surfaces_translucent(make_controller, tmp_path):
    name = import_background(make_image(tmp_path / "a.png"))
    c = make_controller(Settings(background=name, background_visibility=60, background_blur=True))
    assert c.window.central.has_image()
    tokens = theme.current_tokens()
    assert tokens["surface_cell"].startswith("rgba(") and not tokens["surface_cell"].endswith(", 255)")
    c.apply_settings(Settings())                               # bỏ ảnh nền
    assert not c.window.central.has_image()
    assert theme.current_tokens()["surface_cell"].endswith(", 255)")


def test_missing_background_file_is_ignored(make_controller):
    c = make_controller(Settings(background="bg-khongco.jpg"))
    assert not c.window.central.has_image()


def test_settings_choose_background_then_cancel_cleans_up(make_controller, tmp_path, prompts_stub):
    c = make_controller()
    prompts_stub.image = str(make_image(tmp_path / "chon.png"))

    def choose_and_cancel(dialog):
        dialog._choose_background()
        dialog._emit_preview()
        assert c.window.central.has_image()                   # đang xem trước
        return False

    c._exec_dialog = choose_and_cancel
    c.open_settings()
    assert not c.window.central.has_image()
    assert list(paths.backgrounds_dir().iterdir()) == []    # ảnh thử đã bị dọn


def test_settings_choose_background_and_save(make_controller, tmp_path, prompts_stub):
    c = make_controller()
    prompts_stub.image = str(make_image(tmp_path / "chon.png"))

    def choose_and_save(dialog):
        dialog._choose_background()
        dialog.bg_visibility.setValue(70)
        return True

    c._exec_dialog = choose_and_save
    c.open_settings()
    saved = load_settings(paths.settings_path())
    assert saved.background and saved.background_visibility == 70
    assert (paths.backgrounds_dir() / saved.background).exists()
    assert c.window.central.has_image()


# ------------------------------------------------------------------ Tự xoá việc đã xong
def test_auto_delete_from_settings(make_controller, repo):
    old = repo.add_deadline(Deadline("Xong lâu rồi", NOW - timedelta(days=40)))
    repo.set_deadline_status(old.id, DeadlineStatus.DONE, at=NOW - timedelta(days=40))
    recent = repo.add_deadline(Deadline("Mới xong", NOW - timedelta(days=2)))
    repo.set_deadline_status(recent.id, DeadlineStatus.DONE, at=NOW - timedelta(days=2))
    c = make_controller()
    seen = {}

    def enable(dialog):
        dialog.auto_delete.setCurrentIndex(dialog.auto_delete.findData(30))
        seen["warning"] = dialog.purge_warning.text()
        return True

    c._exec_dialog = enable
    c.open_settings()
    assert "1 deadline" in seen["warning"]
    assert repo.get_deadline(old.id) is None and repo.get_deadline(recent.id) is not None


def test_auto_delete_runs_on_timer(make_controller, repo):
    c = make_controller(Settings(auto_delete_done_days=7))
    task = repo.add_deadline(Deadline("A", NOW - timedelta(days=10)))
    repo.set_deadline_status(task.id, DeadlineStatus.DONE, at=NOW - timedelta(days=10))
    c.check_reminders()
    assert repo.get_deadline(task.id) is None


# ------------------------------------------------------------------ Sao lưu / khôi phục
def test_backup_then_restore_through_ui(make_controller, repo, tmp_path, prompts_stub):
    repo.add_deadline(Deadline("Trong bản sao lưu", NOW))
    c = make_controller(Settings(accent="green"))
    c.apply_settings(Settings(accent="green"))
    prompts_stub.save_path = str(tmp_path / "saoluu")        # thiếu đuôi .zip → tự thêm
    backup = c.backup()
    assert backup == tmp_path / "saoluu.zip" and backup.exists() and prompts_stub.infos

    repo.add_deadline(Deadline("Thêm sau khi sao lưu", NOW))
    c.apply_settings(Settings(accent="purple", autostart=True))
    prompts_stub.open_path = str(backup)
    prompts_stub.confirm_answer = False
    assert not c.restore()                                    # bấm Huỷ → không đổi gì
    assert len(repo.search("")) == 2

    prompts_stub.confirm_answer = True
    assert c.restore()
    assert [t.title for t in repo.search("")] == ["Trong bản sao lưu"]
    assert c.settings.accent == "green"
    assert c.settings.autostart is True                       # cài đặt riêng của máy này được giữ
    assert list(paths.backups_dir().glob("truoc-khi-khoi-phuc-*.zip"))


def test_restore_rejects_wrong_file(make_controller, tmp_path, prompts_stub):
    bad = tmp_path / "khong-phai.zip"
    bad.write_text("x")
    prompts_stub.open_path = str(bad)
    assert not make_controller().restore()
    assert prompts_stub.errors and "không phải .zip" in prompts_stub.errors[0]


def test_open_data_folder(make_controller, launcher):
    make_controller().open_data_folder()
    assert launcher.revealed == [(str(paths.settings_path().parent), False)]


# ------------------------------------------------------------------ Đóng gói
def test_ico_has_all_sizes(qapp, tmp_path):
    from app.ui.icon import ICO_SIZES, write_ico

    reader = QImageReader(str(write_ico(tmp_path / "n.ico")))
    assert reader.canRead() and reader.imageCount() == len(ICO_SIZES)


def test_pyinstaller_arguments(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    import build_exe

    args = build_exe.pyinstaller_args(onefile=False, icon=Path("x.ico"))
    assert "--windowed" in args and "--onedir" in args and args[0].endswith("main.py")
    assert args[args.index("--name") + 1] == "Norilan"
    hidden = [args[i + 1] for i, a in enumerate(args) if a == "--hidden-import"]
    assert {"PySide6.QtSvg", "PySide6.QtNetwork"} <= set(hidden)
    excluded = [args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"]
    assert "PySide6.QtNetwork" not in excluded and "PySide6.QtSvg" not in excluded
    assert "--onefile" in build_exe.pyinstaller_args(onefile=True, icon=Path("x.ico"))
    if sys.platform != "win32":
        assert "winotify" not in hidden
