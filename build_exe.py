"""Đóng gói Norilan thành Norilan.exe (chạy trên máy không cài Python).

    python build_exe.py            → dist\\Norilan\\Norilan.exe  (thư mục – khởi động nhanh, khuyên dùng)
    python build_exe.py --onefile  → dist\\Norilan.exe          (một file – tiện gửi, khởi động chậm hơn)

Cần: pip install -r requirements-dev.txt  (có pyinstaller)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "build" / "norilan.ico"

# Module Qt lớn mà Norilan không dùng – loại ra để bản build nhẹ hơn nhiều.
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtHttpServer",
]


def make_icon() -> Path:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    from app.ui.icon import write_ico

    if QGuiApplication.instance() is None:
        make_icon.app = QGuiApplication([])  # type: ignore[attr-defined]  # Qt cần app để vẽ icon
    return write_ico(ICON)


def pyinstaller_args(*, onefile: bool, icon: Path) -> list[str]:
    args = [
        str(ROOT / "main.py"),
        "--name", "Norilan",
        "--windowed",                         # không bật cửa sổ console đen
        "--noconfirm", "--clean",
        "--icon", str(icon),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'app' / 'assets'}{os.pathsep}app/assets",  # icon.png
        "--hidden-import", "PySide6.QtSvg",      # mũi tên/dấu ✓ trong giao diện là SVG
        "--hidden-import", "PySide6.QtNetwork",  # chỉ cho chạy một bản
    ]
    if sys.platform == "win32":
        args += ["--hidden-import", "winotify"]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append("--onefile" if onefile else "--onedir")
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description="Đóng gói Norilan.exe")
    parser.add_argument("--onefile", action="store_true", help="đóng thành một file .exe duy nhất")
    args = parser.parse_args()
    try:
        import PyInstaller.__main__
    except ImportError:
        print("Chưa có PyInstaller. Chạy: pip install -r requirements-dev.txt")
        return 1
    icon = make_icon()
    PyInstaller.__main__.run(pyinstaller_args(onefile=args.onefile, icon=icon))
    target = ROOT / "dist" / ("Norilan.exe" if args.onefile else "Norilan")
    print(f"\nXong! Kết quả: {target}")
    if not args.onefile:
        print("Gửi cho máy khác: nén cả thư mục dist\\Norilan thành .zip rồi giải nén ở máy đó.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
