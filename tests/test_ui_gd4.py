"""GĐ4: khay hệ thống, nhắc việc, cài đặt & màu sắc, single instance (offscreen)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication

from app import paths
from app.files import FileService
from app.instance import InstanceServer, deliver, send_to_running
from app.models import Event, Deadline
from app.notifier import RecordingNotifier
from app.settings import Settings, load_settings
from app.system import RUN_KEY, MemoryRegistry, WindowsIntegration
from app.ui import theme
from app.ui.app_controller import AppController, TrayNotifier

pytestmark = pytest.mark.usefixtures("prompts_stub")
NOW = datetime(2026, 10, 14, 20, 0)


@pytest.fixture
def registry():
    return MemoryRegistry()


@pytest.fixture
def make_controller(qapp, repo, launcher, registry):
    created = []

    def make(settings: Settings | None = None, *, use_tray=False, clock=lambda: NOW):
        controller = AppController(
            qapp, repo, settings or Settings(), settings_path=paths.settings_path(),
            files=FileService(launcher), notifier=RecordingNotifier(),
            integration=WindowsIntegration(registry), clock=clock, use_tray=use_tray,
        )
        created.append(controller)
        return controller

    yield make
    for controller in created:
        controller.shutdown()
        controller.window.quit_app()


# ------------------------------------------------------------------ Khay hệ thống
def test_close_hides_to_tray_and_quit_really_closes(make_controller):
    c = make_controller(use_tray=True)
    c.show_window()
    assert c.window.isVisible()
    c.window.close()
    assert not c.window.isVisible()            # ẩn chứ không thoát
    assert load_settings(paths.settings_path()).tray_hint_shown  # chỉ báo 'vẫn chạy ở khay' lần đầu
    c.show_window()
    assert c.window.isVisible()
    c.window.quit_app()
    assert not c.window.isVisible()


def test_close_without_tray_option(make_controller):
    c = make_controller(Settings(minimize_to_tray=False), use_tray=True)
    assert not c.window.hide_on_close


# ------------------------------------------------------------------ Nhắc việc
def test_check_reminders_sends_digest_and_item(make_controller, repo):
    repo.add_deadline(Deadline("Lab 2", datetime(2026, 10, 15, 9)))
    repo.add_event(Event("Họp", datetime(2026, 10, 14, 20, 30), datetime(2026, 10, 14, 21), remind_before_minutes=60))
    c = make_controller()
    notes = c.check_reminders()
    assert [n.title for n in notes] == ["📅 Họp", "Ngày mai bạn có 1 việc"]
    assert c.notifier.shown == notes
    assert c.check_reminders() == []


def test_reminder_errors_never_crash(make_controller, monkeypatch):
    c = make_controller()
    monkeypatch.setattr(c.reminders, "check", lambda now: 1 / 0)
    assert c.check_reminders() == []


# ------------------------------------------------------------------ Bấm vào toast / lần mở thứ hai
def test_open_url_focuses_item_and_day(make_controller, repo):
    task = repo.add_deadline(Deadline("Lab 2", datetime(2026, 10, 16, 9)))
    c = make_controller()
    c.handle_message({"cmd": "open", "url": f"norilan://item/{task.id}/"})
    assert c.window.isVisible() and c.window.panel.detail_item_id() == task.id
    c.handle_message({"cmd": "open", "url": "norilan://day/2026-10-20"})
    assert c.window.selected_day == date(2026, 10, 20)
    c.handle_message({"cmd": "open", "url": "rác"})
    c.handle_message({"cmd": "ping"})


def test_open_deleted_item_shows_message(make_controller, prompts_stub):
    c = make_controller()
    c.open_url("norilan://item/999")
    assert prompts_stub.errors and "không còn tồn tại" in prompts_stub.errors[0]


def test_tray_fallback_notifier_click_opens_item(make_controller, repo):
    task = repo.add_deadline(Deadline("Lab 2", datetime(2026, 10, 14, 20, 30), remind_before_minutes=60))
    c = make_controller(use_tray=True)
    c.notifier = TrayNotifier(c.tray)
    c.check_reminders()
    c.tray.message_clicked.emit()
    assert c.window.panel.detail_item_id() == task.id


# ------------------------------------------------------------------ Cài đặt & màu sắc
def test_settings_dialog_applies_everything(make_controller, repo, registry):
    repo.add_deadline(Deadline("A", datetime(2026, 10, 15, 9), subject="Toán"))
    c = make_controller()

    def fill(dialog):
        dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
        dialog.accent.set_accent("green", emit=True)
        dialog.color_by.setCurrentIndex(dialog.color_by.findData("type"))
        dialog.autostart.setChecked(True)
        dialog.digest_time.setTime(dialog.digest_time.time().fromString("21:30", "HH:mm"))
        dialog._subject_buttons["Toán"].choose("red")
        return True

    c._exec_dialog = fill
    c.open_settings()
    saved = load_settings(paths.settings_path())
    assert (saved.theme, saved.accent, saved.color_by, saved.daily_digest_time) == ("dark", "green", "type", "21:30")
    assert registry.get(RUN_KEY, "Norilan") is not None
    assert repo.subject_colors()["Toán"] == "red"
    assert theme.current_tokens()["bg"] == theme.DARK["bg"]
    cell = c.window.month_view.cell_for(date(2026, 10, 15))
    assert cell.chip_tones() == ["blue"]  # tô theo loại mục


def test_settings_cancel_reverts_preview(make_controller):
    c = make_controller(Settings(theme="light"))

    def preview_then_cancel(dialog):
        dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
        dialog._emit_preview()  # bỏ qua độ trễ xem trước
        assert theme.current_tokens()["bg"] == theme.DARK["bg"]  # đang xem trước
        return False

    c._exec_dialog = preview_then_cancel
    c.open_settings()
    assert theme.current_tokens()["bg"] == theme.LIGHT["bg"]
    assert c.settings.theme == "light"


def test_chips_colored_by_subject(make_controller, repo):
    repo.add_deadline(Deadline("A", datetime(2026, 10, 15, 9), subject="Toán"))
    repo.add_deadline(Deadline("B", datetime(2026, 10, 15, 10), subject="Lý"))
    repo.add_deadline(Deadline("C", datetime(2026, 10, 15, 11)))
    repo.add_event(Event("D", datetime(2026, 10, 15, 12), datetime(2026, 10, 15, 13), color="pink"))
    c = make_controller()
    tones = c.window.month_view.cell_for(date(2026, 10, 15)).chip_tones()
    colors = repo.subject_colors()                        # màu được gán & lưu cố định
    assert tones[:2] == [colors["Toán"], colors["Lý"]] and colors["Toán"] != colors["Lý"]
    assert tones[2:] == ["gray"]                           # chỉ hiện 3 chip/ô


def test_new_subject_color_from_task_dialog_is_saved(make_controller, repo):
    c = make_controller()

    def fill(dialog):
        dialog.title_edit.setText("Bài mới")
        dialog.subject.setCurrentText("Hoá học")
        dialog.subject_tone.choose("yellow")
        dialog.accept()
        return dialog.result() == 1

    c.window._exec_dialog = fill
    c.window.new_deadline(date(2026, 10, 16))
    assert repo.subject_colors()["Hoá học"] == "yellow"


def test_event_color_from_dialog(make_controller, repo):
    c = make_controller()

    def fill(dialog):
        dialog.title_edit.setText("CLB")
        dialog.color.choose("green")
        dialog.accept()
        return dialog.result() == 1

    c.window._exec_dialog = fill
    c.window.new_event(date(2026, 10, 16))
    assert repo.search("CLB")[0].color == "green"


def test_hover_add_button_creates_task_on_that_day(make_controller):
    c = make_controller()
    seen = {}
    c.window._exec_dialog = lambda dialog: seen.setdefault("due", dialog.due.value()) and False
    c.window.month_view.cell_for(date(2026, 10, 21))._add.click()
    assert seen["due"] == datetime(2026, 10, 21, 23, 59)


def test_theme_system_and_custom_accent(qapp):
    theme.apply_theme(qapp, "system", "#12ab34")
    assert theme.current_tokens()["accent"] == "#12ab34"
    theme.apply_theme(qapp, "light", "blue")


# ------------------------------------------------------------------ Chỉ một bản chạy
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECOND_INSTANCE = '''
import sys
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from app.instance import deliver
print(deliver({"cmd": "open", "url": "norilan://item/1"}, name=sys.argv[1]), flush=True)
'''


def test_no_running_instance(qapp):
    name = f"norilan-test-{uuid.uuid4().hex[:8]}"
    assert deliver({"cmd": "show"}, name=name, timeout_ms=200) == "no-server"
    assert not send_to_running({"cmd": "show"}, name=name, timeout_ms=200)


def test_second_process_forwards_to_running_instance(qapp):
    """Đúng như khi bấm toast: Windows mở thêm một tiến trình Norilan, tiến trình đó chuyển lệnh rồi thoát."""
    name = f"norilan-test-{uuid.uuid4().hex[:8]}"
    server = InstanceServer(name)
    assert server.start()
    received = []
    server.message_received.connect(received.append)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(PROJECT_ROOT)}
    process = subprocess.Popen([sys.executable, "-c", SECOND_INSTANCE, name], cwd=PROJECT_ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 30
    while (process.poll() is None or not received) and time.monotonic() < deadline:
        QCoreApplication.processEvents()  # bản 'đang chạy' phải xử lý sự kiện thì mới nhận được lệnh
        time.sleep(0.01)
    out, err = process.communicate(timeout=10)
    server.close()
    assert received == [{"cmd": "open", "url": "norilan://item/1"}], err
    assert out.strip() == "acked", err


class FakeSocket:
    """Mô phỏng tình huống trên Windows: dữ liệu đã tới và bên gửi đã ngắt TRƯỚC khi server kịp nối tín hiệu."""

    class _Signal:
        def connect(self, _slot):
            pass  # tín hiệu đã phát xong từ trước → không bao giờ gọi lại

    def __init__(self, data: bytes, connected: bool) -> None:
        self._data = data
        self._connected = connected
        self.readyRead = self._Signal()
        self.disconnected = self._Signal()

    def readAll(self):
        data, self._data = self._data, b""
        return data

    def state(self):
        from PySide6.QtNetwork import QLocalSocket

        state = QLocalSocket.LocalSocketState
        return state.ConnectedState if self._connected else state.UnconnectedState

    def deleteLater(self):
        pass

    def write(self, data):
        pass

    def flush(self):
        pass


@pytest.mark.parametrize("connected", [False, True])
def test_server_reads_data_that_arrived_before_signals(qapp, monkeypatch, connected):
    server = InstanceServer(f"norilan-test-{uuid.uuid4().hex[:8]}")
    received = []
    server.message_received.connect(received.append)
    pending = [FakeSocket(b'{"cmd": "open", "url": "norilan://item/7"}\n{"cmd": "show"}', connected)]
    monkeypatch.setattr(server._server, "nextPendingConnection", lambda: pending.pop() if pending else None)
    server._on_connection()
    expected = [{"cmd": "open", "url": "norilan://item/7"}]
    if not connected:
        expected.append({"cmd": "show"})  # dòng cuối không có \n vẫn được xử lý khi bên gửi đã ngắt
    assert received == expected
