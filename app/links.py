"""Đường link nội bộ 'norilan://…' – dùng khi bấm vào toast để mở đúng mục."""
from __future__ import annotations

from datetime import date

SCHEME = "norilan"


def item_url(item_id: int) -> str:
    return f"{SCHEME}://item/{item_id}"


def day_url(day: date) -> str:
    return f"{SCHEME}://day/{day.isoformat()}"


def parse(url: str) -> tuple[str, int | date] | None:
    """'norilan://item/5' → ('item', 5); 'norilan://day/2026-10-15' → ('day', date). Sai định dạng → None.

    Chấp nhận dấu '/' thừa ở cuối (Windows đôi khi tự thêm khi mở link).
    """
    prefix = f"{SCHEME}://"
    url = url.strip().strip('"')
    if not url.lower().startswith(prefix):
        return None
    parts = [p for p in url[len(prefix):].split("/") if p]
    if len(parts) != 2:
        return None
    kind, value = parts[0].lower(), parts[1]
    try:
        if kind == "item":
            return ("item", int(value))
        if kind == "day":
            return ("day", date.fromisoformat(value))
    except ValueError:
        return None
    return None
