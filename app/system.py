"""Tích hợp Windows qua registry (HKCU, không cần quyền admin):
khởi động cùng Windows, giao thức norilan:// (bấm toast) và tên/icon hiển thị trên toast.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Protocol

from app import APP_NAME
from app.links import SCHEME

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROTOCOL_KEY = rf"Software\Classes\{SCHEME}"
APP_USER_MODEL_ID = "Norilan.Desktop"
AUMID_KEY = rf"Software\Classes\AppUserModelId\{APP_USER_MODEL_ID}"
MAIN_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"


class Registry(Protocol):
    def get(self, key: str, name: str) -> str | None: ...
    def set(self, key: str, name: str, value: str) -> None: ...
    def delete(self, key: str, name: str) -> None: ...


class WinRegistry:
    """HKEY_CURRENT_USER thật (chỉ trên Windows)."""

    def __init__(self) -> None:
        import winreg

        self._winreg = winreg

    def get(self, key: str, name: str) -> str | None:
        try:
            with self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, key) as handle:
                return str(self._winreg.QueryValueEx(handle, name)[0])
        except OSError:
            return None

    def set(self, key: str, name: str, value: str) -> None:
        with self._winreg.CreateKeyEx(self._winreg.HKEY_CURRENT_USER, key, 0, self._winreg.KEY_SET_VALUE) as handle:
            self._winreg.SetValueEx(handle, name, 0, self._winreg.REG_SZ, value)

    def delete(self, key: str, name: str) -> None:
        try:
            with self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, key, 0, self._winreg.KEY_SET_VALUE) as handle:
                self._winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass


class MemoryRegistry:
    """Registry giả trong bộ nhớ – cho test và khi chạy trên macOS/Linux."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, key: str, name: str) -> str | None:
        return self.values.get((key, name))

    def set(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def delete(self, key: str, name: str) -> None:
        self.values.pop((key, name), None)


def launch_parts(*args: str) -> list[str]:
    """Lệnh chạy lại chính app: Norilan.exe khi đã đóng gói, pythonw.exe main.py khi chạy từ mã nguồn."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")  # không bật cửa sổ console đen
    return [str(pythonw if pythonw.exists() else python), str(MAIN_SCRIPT), *args]


def command_line(parts: list[str]) -> str:
    return " ".join(f'"{p}"' for p in parts)


class WindowsIntegration:
    def __init__(self, registry: Registry | None = None) -> None:
        if registry is None:
            registry = WinRegistry() if sys.platform == "win32" else MemoryRegistry()
        self._reg = registry

    # --- khởi động cùng Windows
    def autostart_enabled(self) -> bool:
        return self._reg.get(RUN_KEY, APP_NAME) is not None

    def set_autostart(self, enabled: bool) -> None:
        if enabled:
            self._reg.set(RUN_KEY, APP_NAME, command_line(launch_parts("--background")))
        else:
            self._reg.delete(RUN_KEY, APP_NAME)
        log.info("Autostart %s", "on" if enabled else "off")

    # --- bấm toast → mở app
    def register_protocol(self) -> None:
        self._reg.set(PROTOCOL_KEY, "", f"URL:{APP_NAME}")
        self._reg.set(PROTOCOL_KEY, "URL Protocol", "")
        self._reg.set(PROTOCOL_KEY + r"\shell\open\command", "", command_line(launch_parts("--open", "%1")))

    # --- tên + icon hiển thị trên toast
    def register_app_id(self, icon_path: str) -> None:
        self._reg.set(AUMID_KEY, "DisplayName", APP_NAME)
        self._reg.set(AUMID_KEY, "IconUri", icon_path)

    def setup(self, *, icon_path: str, autostart: bool) -> None:
        """Gọi mỗi lần khởi động: cập nhật đường dẫn (nếu bạn di chuyển thư mục dự án/exe)."""
        self.register_protocol()
        self.register_app_id(icon_path)
        self.set_autostart(autostart)
