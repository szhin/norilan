from __future__ import annotations

import sys

from app import system
from app.notifier import RecordingNotifier, powershell_safe, toast_tag
from app.reminders import Notification
from app.system import AUMID_KEY, PROTOCOL_KEY, RUN_KEY, MemoryRegistry, WindowsIntegration, command_line


def test_autostart_toggle():
    reg = MemoryRegistry()
    win = WindowsIntegration(reg)
    assert not win.autostart_enabled()
    win.set_autostart(True)
    assert win.autostart_enabled()
    command = reg.get(RUN_KEY, "Norilan")
    assert command.endswith('"--background"') and "main.py" in command
    win.set_autostart(False)
    assert not win.autostart_enabled()


def test_protocol_and_app_id_registration():
    reg = MemoryRegistry()
    WindowsIntegration(reg).setup(icon_path=r"C:\data\icon.png", autostart=False)
    assert reg.get(PROTOCOL_KEY, "URL Protocol") == ""
    assert reg.get(PROTOCOL_KEY + r"\shell\open\command", "").endswith('"--open" "%1"')
    assert reg.get(AUMID_KEY, "DisplayName") == "Norilan"
    assert reg.get(AUMID_KEY, "IconUri") == r"C:\data\icon.png"


def test_frozen_exe_command(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Norilan\Norilan.exe")
    assert system.launch_parts("--background") == [r"C:\Program Files\Norilan\Norilan.exe", "--background"]
    assert command_line(system.launch_parts("--open", "%1")) == r'"C:\Program Files\Norilan\Norilan.exe" "--open" "%1"'


def test_powershell_safe_escapes_variables_and_terminators():
    assert powershell_safe("Giá $5 `x`") == "Giá `$5 ``x``"
    assert "]]>" not in powershell_safe("a]]>b") and '"@' not in powershell_safe('a"@b')


def test_toast_tag_short_unique_ascii():
    a, b = toast_tag("item-1-202610142359"), toast_tag("item-2-202610142359")
    assert a != b and len(a) <= 16 and a.isascii()


def test_recording_notifier():
    notifier = RecordingNotifier()
    note = Notification("k", "t", "b", "norilan://item/1")
    notifier.show(note)
    assert notifier.shown == [note]
