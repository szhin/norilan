"""Nhập deadline từ ảnh chụp: gửi ảnh cho Gemini (mặc định, có gói miễn phí) hoặc Claude và nhận lại danh sách deadline.

Không phụ thuộc Qt hay SDK ngoài – chỉ dùng urllib. Mỗi người dùng tự nhập API key của mình; key lưu ở
`ai_key.txt` trong thư mục dữ liệu (không nằm trong settings.json nên không đi theo file sao lưu).
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
import os
import time as _time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time

from app import paths
from app.db import fold_text

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"          # Claude
API_VERSION = "2023-06-01"
MODEL = "claude-haiku-5-5"
GEMINI_MODEL = "gemini-2.5-flash"                           # có gói miễn phí, đọc ảnh tốt
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash-lite"              # model dự phòng khi model chính quá tải
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"           # trang tạo khóa Gemini miễn phí
CLAUDE_KEY_PREFIX = "sk-ant-"                               # khóa Claude; khóa khác coi là của Gemini
TIMEOUT_SECONDS = 90
BUSY_STATUSES = (500, 502, 503, 504, 529)   # máy chủ quá tải / lỗi tạm thời ⇒ đáng thử lại
RETRY_DELAYS = (2.0, 5.0)                   # nghỉ giữa các lần thử trên cùng một model (tối đa 3 lần)
MAX_IMAGES = 6
DEFAULT_TIME = time(23, 59)  # ảnh không ghi giờ ⇒ cuối ngày, giống mặc định khi nhập tay
CONFIDENCE_LEVELS = ("high", "medium", "low")
TOOL_NAME = "record_deadlines"

SYSTEM_PROMPT = (
    "You read screenshots or photos of assignment / deadline lists (LMS pages such as Canvas, Moodle, "
    "Google Classroom, school portals, spreadsheets, handwritten notes) in any language. "
    "Extract every deadline you can see and report them with the record_deadlines tool. "
    "Rules: copy course and task names exactly as written (keep the original language and diacritics); "
    "never invent items that are not visible; if the course is not shown, use an empty string; "
    "give due_date as YYYY-MM-DD and due_time as 24-hour HH:MM (null when no time is shown); "
    "when the year is missing, use the nearest occurrence that is not more than about 2 months in the past; "
    "resolve relative words ('tomorrow', 'Friday') from today's date; "
    "set confidence to 'low' when text is blurry, cut off or you had to guess a field, "
    "'medium' when slightly unsure, otherwise 'high'. Ignore items already marked as submitted or completed "
    "only if the page clearly shows that; otherwise include them."
)

_ROW_PROPERTIES = {
    "course": {"type": "string", "description": "Course / subject name, '' if unknown"},
    "title": {"type": "string", "description": "Task / assignment name"},
    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
    "due_time": {"type": ["string", "null"], "description": "HH:MM 24h, null if not shown"},
    "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
    "notes": {"type": "string", "description": "Extra detail worth keeping, '' if none"},
}
_ROW_REQUIRED = ["course", "title", "due_date", "due_time", "confidence"]

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Record the deadlines found in the image(s).",
    "input_schema": {
        "type": "object",
        "properties": {"deadlines": {"type": "array", "items": {
            "type": "object", "properties": _ROW_PROPERTIES, "required": _ROW_REQUIRED}}},
        "required": ["deadlines"],
    },
}

# Gemini dùng tập con OpenAPI: kiểu viết HOA, 'nullable' thay cho ["string", "null"]
GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {"deadlines": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "course": {"type": "STRING"}, "title": {"type": "STRING"},
            "due_date": {"type": "STRING", "description": "YYYY-MM-DD"},
            "due_time": {"type": "STRING", "nullable": True, "description": "HH:MM 24h, null if not shown"},
            "confidence": {"type": "STRING", "enum": list(CONFIDENCE_LEVELS)},
            "notes": {"type": "STRING"},
        },
        "required": _ROW_REQUIRED,
    }}},
    "required": ["deadlines"],
}


class AiImportError(Exception):
    """Lỗi khi nhập từ ảnh. `kind` để UI chọn câu thông báo đã dịch; `detail` là chi tiết kỹ thuật."""

    KINDS = ("no_key", "auth", "rate", "busy", "network", "server", "parse")  # mỗi loại có khoá i18n aiimport.err_<loại>

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


@dataclass(slots=True)
class ImageData:
    media_type: str   # image/png | image/jpeg | image/webp | image/gif
    data: bytes


@dataclass(slots=True)
class ExtractedDeadline:
    course: str
    title: str
    due_at: datetime
    confidence: str = "high"
    notes: str = ""

    @property
    def uncertain(self) -> bool:
        return self.confidence != "high"


# ------------------------------------------------------------------ API key
def key_path():  # type: ignore[no-untyped-def]
    return paths.data_dir() / "ai_key.txt"


def load_api_key() -> str:
    try:
        return key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_api_key(key: str) -> None:
    """Lưu key; chuỗi rỗng ⇒ xoá."""
    key = key.strip()
    path = key_path()
    if not key:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key, encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ Gọi API
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 – URL cố định (https)
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AiImportError("network", str(exc)) from exc


def build_request(images: list[ImageData], *, today: date, language: str = "") -> dict:
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": img.media_type,
                                     "data": base64.b64encode(img.data).decode("ascii")}}
        for img in images
    ]
    hint = f" The user's app language is '{language}'." if language else ""
    content.append({"type": "text", "text": f"Today is {today:%Y-%m-%d} ({today:%A}).{hint} "
                                            "List all deadlines in the image(s)."})
    return {
        "model": MODEL,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "tools": [TOOL_SCHEMA],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": content}],
    }


def _parse_due(date_text: object, time_text: object) -> datetime:
    day = date.fromisoformat(str(date_text).strip()[:10])
    clock = DEFAULT_TIME
    if isinstance(time_text, str) and time_text.strip():
        try:
            clock = time.fromisoformat(time_text.strip()[:5])
        except ValueError:
            clock = DEFAULT_TIME
    return datetime.combine(day, clock)


def _rows_to_deadlines(rows: object) -> list[ExtractedDeadline]:
    """Danh sách dòng JSON (của Claude hay Gemini) → ExtractedDeadline; dòng hỏng bị bỏ qua, không làm hỏng cả lô."""
    if not isinstance(rows, list):
        raise AiImportError("parse", "deadlines is not a list")
    result: list[ExtractedDeadline] = []
    for row in rows:
        try:
            title = str(row["title"]).strip()
            if not title:
                continue
            confidence = row.get("confidence") if row.get("confidence") in CONFIDENCE_LEVELS else "medium"
            result.append(ExtractedDeadline(
                course=str(row.get("course") or "").strip(), title=title,
                due_at=_parse_due(row["due_date"], row.get("due_time")),
                confidence=confidence, notes=str(row.get("notes") or "").strip(),
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            log.warning("Bỏ qua dòng không đọc được: %r", row)
    return result


def parse_response(payload: dict) -> list[ExtractedDeadline]:
    """Phản hồi của Claude Messages API (khối tool_use)."""
    block = next((b for b in payload.get("content", []) if b.get("type") == "tool_use"), None)
    if block is None or not isinstance(block.get("input"), dict):
        raise AiImportError("parse", "no tool_use block")
    return _rows_to_deadlines(block["input"].get("deadlines"))


# ------------------------------------------------------------------ Gemini
def build_gemini_request(images: list[ImageData], *, today: date, language: str = "") -> dict:
    hint = f" The user's app language is '{language}'." if language else ""
    parts: list[dict] = [
        {"inline_data": {"mime_type": img.media_type, "data": base64.b64encode(img.data).decode("ascii")}}
        for img in images
    ]
    parts.append({"text": f"Today is {today:%Y-%m-%d} ({today:%A}).{hint} List all deadlines in the image(s). "
                          'Answer as JSON: {"deadlines": [...]}.'})
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT.replace("with the record_deadlines tool", "as JSON")}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "responseSchema": GEMINI_SCHEMA},
    }


def parse_gemini_response(payload: dict) -> list[ExtractedDeadline]:
    try:
        text = "".join(p.get("text", "") for p in payload["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        reason = (payload.get("promptFeedback") or {}).get("blockReason") if isinstance(payload, dict) else None
        raise AiImportError("parse", f"no candidates{f' ({reason})' if reason else ''}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AiImportError("parse", "model output is not JSON") from exc
    return _rows_to_deadlines(data.get("deadlines") if isinstance(data, dict) else data)


def is_claude_key(api_key: str) -> bool:
    return api_key.strip().startswith(CLAUDE_KEY_PREFIX)


def _check_status(status: int, raw: bytes) -> None:
    text = raw[:400].decode("utf-8", "replace")
    if status in (401, 403) or (status == 400 and ("API_KEY_INVALID" in text or "API key not valid" in text)):
        raise AiImportError("auth", f"HTTP {status}")
    if status == 429:
        raise AiImportError("rate", "HTTP 429")
    if status in BUSY_STATUSES:
        raise AiImportError("busy", f"HTTP {status}")
    if status >= 400:
        raise AiImportError("server", f"HTTP {status}: {' '.join(text.split())[:200]}")


def extract_deadlines(
    images: list[ImageData], api_key: str, *, today: date, language: str = "",
    transport: Transport = _urllib_transport, sleep: Callable[[float], None] = _time.sleep,
) -> list[ExtractedDeadline]:
    """Khóa bắt đầu bằng 'sk-ant-' ⇒ Claude, còn lại ⇒ Gemini.

    Máy chủ quá tải (503…) thì thử lại vài lần, Gemini còn chuyển sang model dự phòng trước khi báo lỗi 'busy'.
    """
    api_key = api_key.strip()
    if not api_key:
        raise AiImportError("no_key")
    if not images:
        return []
    images = images[:MAX_IMAGES]
    if is_claude_key(api_key):
        urls = [API_URL]
        parse = parse_response
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        body = json.dumps(build_request(images, today=today, language=language)).encode("utf-8")
    else:
        urls = [GEMINI_URL.format(model=m) for m in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL)]
        parse = parse_gemini_response
        headers = {"x-goog-api-key": api_key, "content-type": "application/json"}
        body = json.dumps(build_gemini_request(images, today=today, language=language)).encode("utf-8")

    error: AiImportError | None = None
    for url in urls:
        for attempt in range(len(RETRY_DELAYS) + 1):
            if attempt:
                sleep(RETRY_DELAYS[attempt - 1])
            status, raw = transport(url, headers, body, TIMEOUT_SECONDS)
            try:
                _check_status(status, raw)
            except AiImportError as exc:
                if exc.kind != "busy":
                    raise
                error = exc
                log.warning("%s quá tải (%s), lần thử %d", url.split("?")[0], exc.detail, attempt + 1)
                continue
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                raise AiImportError("parse", "response is not JSON") from exc
            return parse(payload)
    raise error or AiImportError("busy")


# ------------------------------------------------------------------ Khớp môn học
def match_subject(name: str, known: list[str]) -> str:
    """Tên môn trong ảnh → tên môn đã có trong app (bỏ dấu/hoa thường, chứa nhau, hoặc gần giống). Không khớp ⇒ giữ nguyên."""
    name = name.strip()
    if not name:
        return ""
    folded = fold_text(name)
    for candidate in known:
        if fold_text(candidate) == folded:
            return candidate
    for candidate in known:
        other = fold_text(candidate)
        if min(len(other), len(folded)) >= 4 and (other in folded or folded in other):
            return candidate
    best = max(known, key=lambda c: difflib.SequenceMatcher(None, fold_text(c), folded).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, fold_text(best), folded).ratio() >= 0.85:
        return best
    return name
