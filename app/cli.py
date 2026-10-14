"""CLI kiểm tra nhanh tầng dữ liệu: python -m app.cli [--seed] [--search TEXT]."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from app import APP_NAME, __version__, paths
from app.db import connect, schema_version
from app.demo_data import seed_demo
from app.logging_setup import setup_logging
from app.models import Task
from app.repository import Repository
from app.settings import load_settings
from app.timeutil import now

log = logging.getLogger(__name__)


def _fmt(item) -> str:  # type: ignore[no-untyped-def]
    when = item.starts_at.strftime("%d/%m %H:%M")
    if isinstance(item, Task):
        extra = f"[{item.status.label}] {item.subject}".strip()
        att = f" 📎{len(item.attachments)}" if item.attachments else ""
        return f"  ✏️  {when}  {item.title}  – {extra}{att}"
    return f"  📅 {when}–{item.ends_at:%H:%M}  {item.title}  @ {item.location or '—'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Norilan – CLI kiểm tra dữ liệu")
    parser.add_argument("--seed", action="store_true", help="thêm dữ liệu mẫu")
    parser.add_argument("--search", metavar="TEXT", help="tìm theo tiêu đề/ghi chú/môn học")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log_file = setup_logging(paths.log_dir())
    log.info("%s %s starting", APP_NAME, __version__)
    settings = load_settings(paths.settings_path())
    conn = connect(paths.db_path())
    repo = Repository(conn)
    current = now()

    if args.seed:
        print(f"Đã thêm {seed_demo(repo, current)} mục mẫu.")

    counts = repo.count_items()
    print(f"{APP_NAME} {__version__}")
    print(f"Thư mục dữ liệu : {paths.data_dir()}")
    print(f"Database        : {paths.db_path()} (schema v{schema_version(conn)})")
    print(f"Log             : {log_file}")
    print(f"Nhắc tổng hợp   : {settings.daily_digest_time}  | theme: {settings.theme}")
    print(f"Tổng            : {counts['task']} task, {counts['event']} event\n")

    if args.search is not None:
        sections = [(f"Kết quả tìm '{args.search}'", repo.search(args.search))]
    else:
        sections = [
            ("Hôm nay", repo.items_on(current.date())),
            ("Ngày mai", repo.items_on(current.date() + timedelta(days=1))),
            ("Quá hạn", repo.overdue_tasks(current)),
            ("Sắp tới (7 ngày)", repo.upcoming(current)),
        ]
    for title, items in sections:
        print(f"{title} ({len(items)})")
        print("\n".join(_fmt(i) for i in items) or "  (trống)")
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
