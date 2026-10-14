"""Dịch vụ đính kèm (không Qt): nhận diện loại, kiểm tra tồn tại, sao chép, mở file/thư mục/link."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Protocol

from app.models import Attachment, AttachmentKind

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class AttachmentMissingError(FileNotFoundError):
    """File/thư mục đính kèm không còn ở đường dẫn đã lưu."""

    def __init__(self, attachment: Attachment) -> None:
        super().__init__(f"Không tìm thấy: {attachment.target}")
        self.attachment = attachment


# ------------------------------------------------------------------ Nhận diện
def looks_like_url(text: str) -> bool:
    text = text.strip()
    return bool(_URL_RE.match(text)) or text.lower().startswith("www.")


def normalize_url(text: str) -> str:
    text = text.strip()
    return f"https://{text}" if text.lower().startswith("www.") else text


def clean_path_text(text: str) -> str:
    """Bỏ khoảng trắng và dấu ngoặc kép (Windows 'Copy as path' tự thêm "...")."""
    return text.strip().strip('"').strip()


def attachment_from_target(target: str, display_name: str = "") -> Attachment:
    """Tạo Attachment từ đường dẫn hoặc URL người dùng chọn/kéo-thả/dán vào."""
    text = clean_path_text(target)
    if not text:
        raise ValueError("Đường dẫn trống")
    if looks_like_url(text):
        return Attachment(normalize_url(text), AttachmentKind.URL, display_name)
    path = os.path.abspath(os.path.expanduser(text))
    kind = AttachmentKind.FOLDER if os.path.isdir(path) else AttachmentKind.FILE
    return Attachment(path, kind, display_name)


def is_missing(att: Attachment) -> bool:
    """True nếu file/thư mục không còn tồn tại. URL luôn coi là còn."""
    if att.kind == AttachmentKind.URL:
        return False
    try:
        if att.kind == AttachmentKind.FOLDER:
            return not os.path.isdir(att.target)
        return not os.path.isfile(att.target)
    except (OSError, ValueError):
        return True


def nearest_existing_dir(path: str) -> Path | None:
    """Thư mục cha gần nhất còn tồn tại – để mở hộp thoại 'chọn lại' ở đúng chỗ."""
    current = Path(path)
    if current.is_dir():
        return current
    for parent in current.parents:
        if parent.is_dir():
            return parent
    return None


# ------------------------------------------------------------------ Sao chép
def unique_path(directory: Path, name: str) -> Path:
    """'bai.docx' → 'bai (1).docx' nếu đã tồn tại."""
    candidate = directory / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def copy_into(source: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = unique_path(directory, source.name)
    shutil.copy2(source, destination)
    log.info("Copied attachment %s → %s", source, destination)
    return destination


def is_inside(path: str | Path, directory: Path) -> bool:
    """path có nằm trong directory (kể cả thư mục con) không."""
    try:
        return directory.resolve() in Path(path).resolve().parents
    except (OSError, ValueError):
        return False


def is_app_copy(att: Attachment, dest_dir: Path) -> bool:
    """Đính kèm này là bản sao Norilan đang giữ (nằm trong thư mục attachments của app)."""
    return att.kind == AttachmentKind.FILE and is_inside(att.target, dest_dir)


def prepare_attachments(attachments: list[Attachment], *, copy_files: bool, dest_dir: Path) -> list[str]:
    """Nếu bật copy: chép MỌI file đính kèm (cả cũ lẫn mới) vào dest_dir và trỏ đính kèm sang bản sao.

    Bỏ qua file đã là bản sao, file đã mất, thư mục và link.
    Trả về danh sách lỗi (file nào copy lỗi thì giữ đường dẫn gốc).
    """
    errors: list[str] = []
    if not copy_files:
        return errors
    dest = dest_dir.resolve()
    for att in attachments:
        if att.kind != AttachmentKind.FILE or is_missing(att) or is_inside(att.target, dest):
            continue
        source = Path(att.target)
        try:
            att.target = str(copy_into(source, dest))
        except OSError as exc:
            log.warning("Copy failed for %s: %s", source, exc)
            errors.append(f"{source.name}: {exc.strerror or exc}")
    return errors


def remove_orphan_copies(dest_dir: Path, referenced: set[str]) -> list[Path]:
    """Xoá các bản sao trong dest_dir không còn đính kèm nào trỏ tới. Trả về các file đã xoá.

    Chỉ đụng tới file trong thư mục attachments của app – không bao giờ xoá file gốc của người dùng.
    """
    if not dest_dir.is_dir():
        return []
    keep = {os.path.normcase(os.path.abspath(p)) for p in referenced}
    removed: list[Path] = []
    for file in dest_dir.rglob("*"):
        if file.is_file() and os.path.normcase(os.path.abspath(file)) not in keep:
            try:
                file.unlink()
                removed.append(file)
            except OSError as exc:
                log.warning("Không xoá được bản sao %s: %s", file, exc)
    if removed:
        log.info("Removed %d orphan attachment copies", len(removed))
    return removed


# ------------------------------------------------------------------ Mở
class Launcher(Protocol):
    def open(self, target: str) -> None: ...
    def reveal(self, path: str, *, select: bool) -> None: ...


class SystemLauncher:
    """Mở bằng ứng dụng mặc định của hệ điều hành."""

    def open(self, target: str) -> None:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]  # Word, PDF, trình duyệt...
        elif looks_like_url(target):
            webbrowser.open(target)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])

    def reveal(self, path: str, *, select: bool) -> None:
        if sys.platform == "win32":
            if select:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if select else ["open", path])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent) if select else path])


class FileService:
    def __init__(self, launcher: Launcher | None = None) -> None:
        self._launcher = launcher or SystemLauncher()

    def open(self, att: Attachment) -> None:
        """Mở đính kèm. Raise AttachmentMissingError nếu file đã mất, OSError nếu Windows không mở được."""
        if is_missing(att):
            raise AttachmentMissingError(att)
        log.info("Opening %s", att.target)
        self._launcher.open(att.target)

    def open_folder(self, path: str) -> None:
        """Mở một thư mục trong Explorer (vd. thư mục dữ liệu của Norilan)."""
        self._launcher.reveal(path, select=False)

    def reveal(self, att: Attachment) -> str:
        """Mở thư mục chứa file (chọn sẵn file). File mất thì mở thư mục cha gần nhất. Trả về đường dẫn đã mở."""
        if att.kind == AttachmentKind.URL:
            raise ValueError("Link web không có thư mục chứa")
        if not is_missing(att):
            if att.kind == AttachmentKind.FOLDER:
                self._launcher.reveal(att.target, select=False)
            else:
                self._launcher.reveal(att.target, select=True)
            return att.target
        parent = nearest_existing_dir(att.target)
        if parent is None:
            raise AttachmentMissingError(att)
        self._launcher.reveal(str(parent), select=False)
        return str(parent)
