from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

from app import paths
from app.db import connect
from app.repository import Repository

# Test giao diện chạy ẩn, không mở cửa sổ thật.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def repo(conn) -> Repository:
    return Repository(conn)


@pytest.fixture
def now() -> datetime:
    """'Bây giờ' cố định cho test: 14/10/2026 10:00."""
    return datetime(2026, 10, 14, 10, 0)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Không bao giờ để test ghi vào %APPDATA% thật."""
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def vietnamese_ui():
    """Mọi test bắt đầu (và kết thúc) bằng tiếng Việt – test đổi ngôn ngữ không ảnh hưởng test khác."""
    from app import i18n

    i18n.set_language("vi")
    yield
    i18n.set_language("vi")


# ------------------------------------------------------------------ Dùng cho test giao diện
class FakeLauncher:
    """Thay cho os.startfile / Explorer: chỉ ghi lại đã mở gì."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.revealed: list[tuple[str, bool]] = []
        self.fail: Exception | None = None

    def open(self, target: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.opened.append(target)

    def reveal(self, path: str, *, select: bool) -> None:
        self.revealed.append((path, select))


class PromptStub:
    """Thay cho các hộp thoại hệ thống (QMessageBox, QFileDialog...) – trả lời theo cấu hình."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.confirms: list[str] = []
        self.confirm_answer = True
        self.missing_action = "cancel"
        self.replacement: str | None = None
        self.files: list[str] = []
        self.folder: str | None = None
        self.link: str | None = None
        self.image: str | None = None
        self.save_path: str | None = None
        self.open_path: str | None = None
        self.infos: list[str] = []
        self.color: str | None = None
        self.gradient: str | None = None
        self.gradient_starts: list[str | None] = []


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture(scope="session")
def qapp():
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from app.ui.theme import apply_theme

    app = widgets.QApplication.instance() or widgets.QApplication([])
    apply_theme(app, "light")
    return app


@pytest.fixture
def prompts_stub(monkeypatch) -> PromptStub:
    from app.ui import prompts

    stub = PromptStub()

    def confirm(parent, title, text, ok_text=None):
        stub.confirms.append(text)
        return stub.confirm_answer

    def pick_gradient(parent, start=None):
        stub.gradient_starts.append(start)
        return stub.gradient

    monkeypatch.setattr(prompts, "show_error", lambda parent, text, details="": stub.errors.append(text))
    monkeypatch.setattr(prompts, "confirm", confirm)
    monkeypatch.setattr(prompts, "ask_missing_action", lambda parent, att: stub.missing_action)
    monkeypatch.setattr(prompts, "choose_replacement", lambda parent, att: stub.replacement)
    monkeypatch.setattr(prompts, "pick_files", lambda parent: list(stub.files))
    monkeypatch.setattr(prompts, "pick_folder", lambda parent: stub.folder)
    monkeypatch.setattr(prompts, "ask_link", lambda parent: stub.link)
    monkeypatch.setattr(prompts, "pick_image", lambda parent: stub.image)
    monkeypatch.setattr(prompts, "ask_save_path", lambda parent, title, default, flt: stub.save_path)
    monkeypatch.setattr(prompts, "ask_open_path", lambda parent, title, flt: stub.open_path)
    monkeypatch.setattr(prompts, "show_info", lambda parent, text, details="": stub.infos.append(text))
    monkeypatch.setattr(prompts, "pick_color", lambda parent, start, title=None: stub.color)
    monkeypatch.setattr(prompts, "pick_gradient", pick_gradient)
    return stub


@pytest.fixture(autouse=True)
def _dispose_qt_widgets():
    """Huỷ hẳn mọi cửa sổ sau mỗi test.

    Widget còn sống thì mỗi lần setStyleSheet (đổi theme) Qt phải áp style lại cho tất cả,
    làm các test sau chậm dần. App thật chỉ có một cửa sổ nên không gặp vấn đề này.
    """
    yield
    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    if app is None:
        return
    import shiboken6

    for widget in app.topLevelWidgets():
        if shiboken6.isValid(widget):
            widget.hide()
            shiboken6.delete(widget)
