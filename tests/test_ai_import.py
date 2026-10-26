"""Nhập deadline từ ảnh: parse phản hồi API, lỗi mạng/khóa, khớp môn, dialog xem trước và luồng trong MainWindow."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app import ai_import
from app.ai_import import AiImportError, ExtractedDeadline, ImageData, extract_deadlines, match_subject, parse_response
from app.models import Deadline

TODAY = date(2026, 10, 14)
PNG = ImageData("image/png", b"\x89PNG fake")


def tool_payload(rows):
    return {"content": [{"type": "text", "text": "ok"},
                        {"type": "tool_use", "name": ai_import.TOOL_NAME, "input": {"deadlines": rows}}]}


def row(course="Giải tích", title="Bài tập 3", due_date="2026-10-20", due_time="23:59", confidence="high", **extra):
    return {"course": course, "title": title, "due_date": due_date, "due_time": due_time,
            "confidence": confidence, **extra}


def transport_returning(status, body):
    calls = []

    def transport(url, headers, data, timeout):
        calls.append((url, headers, json.loads(data)))
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()

    transport.calls = calls
    return transport


# ------------------------------------------------------------------ parse_response
def test_parse_rows_with_and_without_time():
    found = parse_response(tool_payload([row(), row(title="Lab", due_time=None, confidence="low", notes=" nộp qua LMS ")]))
    assert [d.title for d in found] == ["Bài tập 3", "Lab"]
    assert found[0].due_at == datetime(2026, 10, 20, 23, 59)
    assert found[1].due_at == datetime(2026, 10, 20, 23, 59)       # không có giờ ⇒ cuối ngày
    assert found[1].uncertain and not found[0].uncertain
    assert found[1].notes == "nộp qua LMS"


def test_parse_skips_broken_rows_but_keeps_the_rest():
    found = parse_response(tool_payload([row(due_date="không rõ"), {"title": "thiếu ngày"}, row(title="  "), row()]))
    assert [d.title for d in found] == ["Bài tập 3"]


def test_parse_unknown_confidence_becomes_medium_and_bad_time_falls_back():
    found = parse_response(tool_payload([row(confidence="???", due_time="sáng")]))
    assert found[0].confidence == "medium" and found[0].due_at.time().hour == 23


@pytest.mark.parametrize("payload", [{}, {"content": [{"type": "text", "text": "xin lỗi"}]},
                                     tool_payload("không phải list")])
def test_parse_without_usable_tool_output_raises(payload):
    with pytest.raises(AiImportError) as exc:
        parse_response(payload)
    assert exc.value.kind == "parse"


# ------------------------------------------------------------------ extract_deadlines
def test_extract_sends_images_key_date_and_forces_the_tool():
    transport = transport_returning(200, tool_payload([row()]))
    found = extract_deadlines([PNG], " sk-ant-test ", today=TODAY, language="vi", transport=transport)
    assert len(found) == 1
    url, headers, body = transport.calls[0]
    assert url == ai_import.API_URL and headers["x-api-key"] == "sk-ant-test"
    assert body["tool_choice"] == {"type": "tool", "name": ai_import.TOOL_NAME}
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert "2026-10-14" in content[-1]["text"] and "'vi'" in content[-1]["text"]


def test_extract_caps_number_of_images():
    transport = transport_returning(200, tool_payload([]))
    extract_deadlines([PNG] * 10, "sk-ant-k", today=TODAY, transport=transport)
    sent = [c for c in transport.calls[0][2]["messages"][0]["content"] if c["type"] == "image"]
    assert len(sent) == ai_import.MAX_IMAGES


@pytest.mark.parametrize("status, kind", [(401, "auth"), (403, "auth"), (429, "rate"), (500, "busy"), (529, "busy"), (404, "server")])
def test_extract_maps_http_errors(status, kind):
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "sk-ant-k", today=TODAY, transport=transport_returning(status, b"{}"), sleep=lambda s: None)
    assert exc.value.kind == kind


def test_extract_without_key_does_not_call_network():
    transport = transport_returning(200, {})
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "   ", today=TODAY, transport=transport)
    assert exc.value.kind == "no_key" and not transport.calls


def test_extract_non_json_body_is_parse_error():
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "sk-ant-k", today=TODAY, transport=transport_returning(200, b"<html>"))
    assert exc.value.kind == "parse"


# ------------------------------------------------------------------ Gemini
def gemini_payload(rows, *, wrap=True):
    text = json.dumps({"deadlines": rows} if wrap else rows)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_key_goes_to_gemini_with_header_schema_and_images():
    transport = transport_returning(200, gemini_payload([row(), row(title="Lab", due_time=None)]))
    found = extract_deadlines([PNG], " AIzaTestKey ", today=TODAY, language="en", transport=transport)
    assert [d.title for d in found] == ["Bài tập 3", "Lab"]
    url, headers, body = transport.calls[0]
    assert url == ai_import.GEMINI_URL.format(model=ai_import.GEMINI_MODEL) and "AIzaTestKey" not in url
    assert headers["x-goog-api-key"] == "AIzaTestKey" and "x-api-key" not in headers
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseSchema"] == ai_import.GEMINI_SCHEMA
    parts = body["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png" and "2026-10-14" in parts[-1]["text"]


def test_claude_prefix_is_routed_to_claude():
    transport = transport_returning(200, tool_payload([row()]))
    extract_deadlines([PNG], "sk-ant-abc", today=TODAY, transport=transport)
    assert transport.calls[0][0] == ai_import.API_URL


@pytest.mark.parametrize("status, body, kind", [
    (400, b'{"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}', "auth"),
    (400, b'{"error": {"message": "API key not valid. Please pass a valid API key."}}', "auth"),
    (403, b"{}", "auth"), (429, b"{}", "rate"),
    (400, b'{"error": {"message": "bad request"}}', "server"), (503, b"{}", "busy"),
])
def test_gemini_http_errors(status, body, kind):
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "AIza", today=TODAY, transport=transport_returning(status, body), sleep=lambda s: None)
    assert exc.value.kind == kind


@pytest.mark.parametrize("payload", [
    {}, {"candidates": []}, {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": [{"content": {"parts": [{"text": "không phải json"}]}}]},
    {"candidates": [{"content": {"parts": [{"text": '{"deadlines": "x"}'}]}}]},
])
def test_gemini_unusable_output_is_parse_error(payload):
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "AIza", today=TODAY, transport=transport_returning(200, payload))
    assert exc.value.kind == "parse"


def test_gemini_skips_broken_rows_and_accepts_bare_list():
    found = extract_deadlines([PNG], "AIza", today=TODAY, transport=transport_returning(
        200, gemini_payload([row(due_date="??"), row()], wrap=False)))
    assert [d.title for d in found] == ["Bài tập 3"]


def test_api_key_roundtrip_and_clear():
    assert ai_import.load_api_key() == ""
    ai_import.save_api_key("  sk-abc \n")
    assert ai_import.load_api_key() == "sk-abc"
    ai_import.save_api_key("")
    assert ai_import.load_api_key() == ""
    assert not ai_import.key_path().exists()


# ------------------------------------------------------------------ match_subject
KNOWN = ["Toán cao cấp", "Phương pháp tính", "Tiếng Anh"]


@pytest.mark.parametrize("seen, expected", [
    ("toan cao cap", "Toán cao cấp"),            # khác dấu / hoa thường
    ("Phương pháp tính 2026", "Phương pháp tính"),  # tên trong ảnh dài hơn
    ("Tieng Anh", "Tiếng Anh"),
    ("Vật lý đại cương", "Vật lý đại cương"),    # môn mới: giữ nguyên
    ("", ""),
])
def test_match_subject(seen, expected):
    assert match_subject(seen, KNOWN) == expected


def test_match_subject_short_names_do_not_match_by_containment():
    assert match_subject("Anh", ["Tiếng Anh"]) == "Anh"


# ------------------------------------------------------------------ Repository.deadline_exists
def test_deadline_exists_ignores_case_and_accents_but_needs_same_due(repo):
    due = datetime(2026, 10, 20, 23, 59)
    repo.add_deadline(Deadline("Bài tập 3", due))
    assert repo.deadline_exists("bai tap 3", due)
    assert not repo.deadline_exists("Bài tập 3", datetime(2026, 10, 21, 23, 59))
    assert not repo.deadline_exists("Bài tập 4", due)


# ------------------------------------------------------------------ Dialog
pytest.importorskip("PySide6")
from PySide6.QtGui import QColor, QImage  # noqa: E402

from app.files import FileService  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.ui.ai_import_dialog import AiImportDialog, encode_image, sync_runner  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.usefixtures("qapp")
NOW = datetime(2026, 10, 14, 10, 0)


def solid(width=40, height=30):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    return image


def found_rows():
    return [
        ExtractedDeadline("toan cao cap", "Bài 1", datetime(2026, 10, 20, 9, 0)),
        ExtractedDeadline("Vật lý", "Báo cáo", datetime(2026, 10, 18, 23, 59), confidence="low"),
        ExtractedDeadline("Tiếng Anh", "Essay", datetime(2026, 10, 25, 12, 0)),
    ]


def make_dialog(extract=lambda images: found_rows(), duplicate=lambda title, due: False):
    return AiImportDialog(subjects=KNOWN, today=TODAY, extract=extract, is_duplicate=duplicate, runner=sync_runner)


def test_encode_image_downsizes_and_returns_png():
    data = encode_image(solid(4000, 100))
    assert data.media_type == "image/png" and data.data.startswith(b"\x89PNG")
    out = QImage.fromData(data.data)
    assert out.width() == 1800


def test_dialog_analyze_button_needs_an_image_and_limits_count():
    dialog = make_dialog()
    assert not dialog.analyze_button.isEnabled()
    for i in range(ai_import.MAX_IMAGES):
        assert dialog.add_image(solid(), f"a{i}.png")
    assert not dialog.add_image(solid(), "extra.png")
    assert dialog.image_count() == ai_import.MAX_IMAGES and dialog.analyze_button.isEnabled()
    assert not dialog.error_label.isHidden()


def test_dialog_rejects_unreadable_image():
    dialog = make_dialog()
    assert not dialog.add_image(QImage(), "bad.png")
    assert dialog.image_count() == 0 and not dialog.error_label.isHidden()


def test_dialog_review_page_fills_sorted_rows_and_matches_subjects():
    dialog = make_dialog()
    dialog.add_image(solid(), "a.png")
    dialog.analyze()
    assert dialog.stack.currentIndex() == dialog.PAGE_REVIEW
    result = dialog.deadlines()
    assert [d.title for d in result] == ["Báo cáo", "Bài 1", "Essay"]          # theo hạn nộp
    assert [d.subject for d in result] == ["Vật lý", "Toán cao cấp", "Tiếng Anh"]  # 'toan cao cap' khớp môn có sẵn
    assert dialog.save_button.text() == "Thêm 3 deadline"


def test_dialog_uncheck_and_edit_rows_change_result():
    dialog = make_dialog()
    dialog.add_image(solid(), "a.png")
    dialog.analyze()
    dialog._rows[0]["check"].setChecked(False)
    dialog._rows[1]["title"].setText("Bài 1 (sửa)")
    result = dialog.deadlines()
    assert [d.title for d in result] == ["Bài 1 (sửa)", "Essay"]
    assert dialog.save_button.text() == "Thêm 2 deadline"
    for r in dialog._rows:
        r["check"].setChecked(False)
    assert not dialog.save_button.isEnabled()


def test_dialog_duplicates_start_unchecked_and_uncertain_is_flagged():
    dialog = make_dialog(duplicate=lambda title, due: title == "Essay")
    dialog.add_image(solid(), "a.png")
    dialog.analyze()
    assert [d.title for d in dialog.deadlines()] == ["Báo cáo", "Bài 1"]
    notes = [dialog.table.cellWidget(i, 6).text() for i in range(3)]
    assert notes == ["⚠ Chưa chắc", "", "Đã có"]


@pytest.mark.parametrize("kind, fragment", [("no_key", "khóa API"), ("auth", "không hợp lệ"),
                                            ("network", "kết nối"), ("rate", "giới hạn")])
def test_dialog_shows_translated_error_and_stays_on_pick_page(kind, fragment):
    def fail(images):
        raise AiImportError(kind)

    dialog = make_dialog(extract=fail)
    dialog.add_image(solid(), "a.png")
    dialog.analyze()
    assert dialog.stack.currentIndex() == dialog.PAGE_PICK
    assert fragment in dialog.error_label.text()
    assert dialog.analyze_button.isEnabled()      # thử lại được


def test_dialog_empty_result_shows_message():
    dialog = make_dialog(extract=lambda images: [])
    dialog.add_image(solid(), "a.png")
    dialog.analyze()
    assert dialog.stack.currentIndex() == dialog.PAGE_PICK and "Không tìm thấy" in dialog.error_label.text()


def test_dialog_remove_selected_image():
    dialog = make_dialog()
    dialog.add_image(solid(), "a.png")
    dialog.add_image(solid(), "b.png")
    dialog.image_list.item(0).setSelected(True)
    dialog.remove_selected()
    assert dialog.image_count() == 1 and dialog.image_list.count() == 1


# ------------------------------------------------------------------ MainWindow
@pytest.fixture
def window(repo, launcher, prompts_stub):
    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: NOW)
    yield win
    win.close()
    win.deleteLater()


def test_new_deadline_cancelled_choice_does_nothing(window, prompts_stub, repo):
    prompts_stub.deadline_source = None
    opened = []
    window._exec_dialog = lambda d: opened.append(d) or False
    window.new_deadline()
    assert opened == [] and repo.subjects() == []


def test_new_deadline_image_without_key_asks_for_key_then_stops(window, prompts_stub):
    prompts_stub.deadline_source = "image"
    opened = []
    window._exec_dialog = lambda d: opened.append(d) or False
    window.new_deadline()
    assert prompts_stub.api_key_asked == 1 and opened == []


def test_image_import_saves_checked_rows_and_assigns_colors_to_new_subjects(window, prompts_stub, repo, monkeypatch):
    ai_import.save_api_key("sk-test")
    prompts_stub.deadline_source = "image"
    repo.add_deadline(Deadline("Cũ", NOW, subject="Toán cao cấp"))
    monkeypatch.setattr(ai_import, "extract_deadlines", lambda images, key, **kw: found_rows())
    monkeypatch.setattr("app.ui.ai_import_dialog.thread_runner", sync_runner)

    def run(dialog):
        dialog._runner = sync_runner
        dialog.add_image(solid(), "a.png")
        dialog.analyze()
        dialog._rows[0]["check"].setChecked(False)       # bỏ 'Báo cáo' (Vật lý, hạn sớm nhất)
        dialog.accept()
        return True

    window._exec_dialog = run
    window.new_deadline()
    saved = {d.title: d for d in repo.items_for_subject("Toán cao cấp", Deadline.KIND)}
    assert "Bài 1" in saved                                   # khớp môn có sẵn, không tạo môn trùng
    assert repo.deadline_exists("Essay", datetime(2026, 10, 25, 12, 0))
    assert not repo.deadline_exists("Báo cáo", datetime(2026, 10, 18, 23, 59))
    assert "Vật lý" not in repo.subject_colors()             # dòng bị bỏ chọn ⇒ môn mới không được tạo
    assert "Tiếng Anh" in repo.subject_colors()              # môn mới có màu riêng ngay


def test_image_import_cancel_saves_nothing(window, prompts_stub, repo):
    ai_import.save_api_key("sk-test")
    prompts_stub.deadline_source = "image"
    window._exec_dialog = lambda d: False
    window.new_deadline()
    assert repo.subjects() == []


def _wait_for(predicate, timeout_ms=3000):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest

    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(20)
        QCoreApplication.processEvents()
        waited += 20
    return predicate()


def test_dialog_with_real_background_thread_success_and_failure():
    from app.ui.ai_import_dialog import thread_runner

    ok = AiImportDialog(subjects=KNOWN, today=TODAY, extract=lambda images: found_rows(), runner=thread_runner)
    ok.add_image(solid(), "a.png")
    ok.analyze()
    assert not ok.analyze_button.isEnabled()                    # đang bận: không bấm lần hai được
    assert _wait_for(lambda: ok.stack.currentIndex() == ok.PAGE_REVIEW)

    def boom(images):
        raise RuntimeError("lỗi lạ")

    bad = AiImportDialog(subjects=KNOWN, today=TODAY, extract=boom, runner=thread_runner)
    bad.add_image(solid(), "a.png")
    bad.analyze()
    assert _wait_for(lambda: not bad.error_label.isHidden())
    assert bad.stack.currentIndex() == bad.PAGE_PICK and bad.analyze_button.isEnabled()


# ------------------------------------------------------------------ Hộp nhập khóa
def test_linkify_makes_key_page_domains_clickable():
    from app.ui.ai_import_dialog import linkify

    out = linkify("vào aistudio.google.com/apikey, hoặc console.anthropic.com. <b>x</b>")
    assert '<a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>' in out
    assert '<a href="https://console.anthropic.com">console.anthropic.com</a>.' in out
    assert "&lt;b&gt;" in out                       # văn bản còn lại được escape


def test_api_key_dialog_opens_default_browser_and_returns_trimmed_key(monkeypatch):
    from PySide6.QtGui import QDesktopServices

    from app.ui.ai_import_dialog import ApiKeyDialog

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    dialog = ApiKeyDialog("old")
    dialog.open_button.click()
    assert opened == [ai_import.GEMINI_KEY_URL]
    assert dialog.key_edit.text() == "old"
    dialog.key_edit.setText("  AIzaNew  ")
    assert dialog.key() == "AIzaNew"


# ------------------------------------------------------------------ Quá tải: thử lại và model dự phòng
def scripted_transport(*responses):
    calls = []
    queue = list(responses)

    def transport(url, headers, data, timeout):
        calls.append(url)
        status, body = queue.pop(0)
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()

    transport.calls = calls
    return transport


def test_busy_then_success_retries_same_model_with_backoff():
    transport = scripted_transport((503, b"{}"), (503, b"{}"), (200, gemini_payload([row()])))
    sleeps = []
    found = extract_deadlines([PNG], "AIza", today=TODAY, transport=transport, sleep=sleeps.append)
    assert len(found) == 1 and sleeps == list(ai_import.RETRY_DELAYS)
    assert len(set(transport.calls)) == 1 and ai_import.GEMINI_MODEL in transport.calls[0]


def test_gemini_falls_back_to_lite_model_when_primary_stays_busy():
    transport = scripted_transport((503, b"{}"), (503, b"{}"), (503, b"{}"), (200, gemini_payload([row()])))
    found = extract_deadlines([PNG], "AIza", today=TODAY, transport=transport, sleep=lambda s: None)
    assert len(found) == 1
    assert ai_import.GEMINI_MODEL in transport.calls[0] and ai_import.GEMINI_FALLBACK_MODEL in transport.calls[-1]


def test_everything_busy_raises_busy_after_all_attempts():
    transport = scripted_transport(*[(503, b"{}")] * 6)
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "AIza", today=TODAY, transport=transport, sleep=lambda s: None)
    assert exc.value.kind == "busy" and len(transport.calls) == 6


def test_non_busy_errors_are_not_retried():
    transport = scripted_transport((429, b"{}"))
    with pytest.raises(AiImportError) as exc:
        extract_deadlines([PNG], "AIza", today=TODAY, transport=transport, sleep=lambda s: pytest.fail("no retry"))
    assert exc.value.kind == "rate" and len(transport.calls) == 1


def test_claude_busy_retries_only_on_claude_url():
    transport = scripted_transport((529, b"{}"), (200, tool_payload([row()])))
    assert len(extract_deadlines([PNG], "sk-ant-x", today=TODAY, transport=transport, sleep=lambda s: None)) == 1
    assert set(transport.calls) == {ai_import.API_URL}


# ------------------------------------------------------------------ Nhắc trước & Study time trong bảng xem trước
def reviewed(extract=lambda images: found_rows()):
    dialog = make_dialog(extract=extract)
    dialog.add_image(solid(), "a.png")
    dialog.analyze()
    return dialog


def test_review_has_remind_and_study_columns_and_nothing_set_by_default():
    dialog = reviewed()
    headers = [dialog.table.horizontalHeaderItem(i).text() for i in range(dialog.table.columnCount())]
    assert headers[4:6] == ["Nhắc trước", "Study time"]
    assert all(e.deadline.remind_before_minutes is None and e.study is None for e in dialog.entries())


def test_remind_before_is_per_row_and_saved_in_minutes():
    dialog = reviewed()
    remind = dialog._rows[1]["remind"]                  # dòng 'Bài 1'
    remind.enabled.setChecked(True)
    remind.amount.setValue(2)
    remind.unit.setCurrentIndex(remind.unit.findData(1440))   # 2 ngày
    by_title = {e.deadline.title: e.deadline for e in dialog.entries()}
    assert by_title["Bài 1"].remind_before_minutes == 2 * 1440
    assert by_title["Báo cáo"].remind_before_minutes is None


def test_default_study_slot_is_evening_before_due_but_not_in_the_past():
    dialog = reviewed()
    start, end = dialog.default_study_slot(datetime(2026, 10, 20, 9, 0))
    assert (start, end) == (datetime(2026, 10, 19, 19, 0), datetime(2026, 10, 19, 20, 0))
    start, _ = dialog.default_study_slot(datetime(2026, 10, 14, 9, 0))      # hạn là hôm nay ⇒ không lùi về quá khứ
    assert start == datetime(2026, 10, 14, 19, 0)


def test_set_study_shows_time_on_button_and_clearing_resets_it():
    dialog = reviewed()
    entry = dialog._rows[0]
    slot = (datetime(2026, 10, 17, 19, 0), datetime(2026, 10, 17, 21, 0))
    dialog.set_study(entry, slot)
    assert entry["study_button"].text() == "📖 17/10 19:00"
    assert [e.study for e in dialog.entries()][0] == slot
    dialog.set_study(entry, None)
    assert entry["study_button"].text() == "📖 Đặt giờ" and dialog.entries()[0].study is None


def test_unchecked_row_drops_its_study_time_too():
    dialog = reviewed()
    dialog.set_study(dialog._rows[0], (datetime(2026, 10, 17, 19, 0), datetime(2026, 10, 17, 20, 0)))
    dialog._rows[0]["check"].setChecked(False)
    assert all(e.study is None for e in dialog.entries())


def test_study_slot_dialog_validates_clears_and_keeps_duration():
    from app.ui.ai_import_dialog import StudySlotDialog

    start = datetime(2026, 10, 17, 19, 0)
    dialog = StudySlotDialog("Bài 1", start, start.replace(hour=20), has_value=True)
    assert dialog.value() == (start, start.replace(hour=20))
    dialog.start.set_value(start.replace(hour=15))                     # dời giờ bắt đầu ⇒ giữ độ dài 1 giờ
    assert dialog.end.value() == start.replace(hour=16)
    dialog.end.set_value(start.replace(hour=14))                       # kết thúc trước bắt đầu ⇒ báo lỗi, không đóng
    dialog.accept()
    assert not dialog.error_label.isHidden() and dialog.result() != 1
    dialog.clear_button.click()
    assert dialog.value() is None and dialog.result() == 1


def test_study_slot_dialog_clear_disabled_when_nothing_to_clear():
    from app.ui.ai_import_dialog import StudySlotDialog

    start = datetime(2026, 10, 17, 19, 0)
    assert not StudySlotDialog("x", start, start.replace(hour=20), has_value=False).clear_button.isEnabled()


def test_image_import_saves_reminder_and_creates_linked_study_time_only_when_set(window, prompts_stub, repo, monkeypatch):
    ai_import.save_api_key("AIza-test")
    prompts_stub.deadline_source = "image"
    repo.add_deadline(Deadline("Cũ", NOW, subject="Toán cao cấp"))

    def run(dialog):
        dialog._runner = sync_runner
        dialog._extract = lambda images: found_rows()
        dialog.add_image(solid(), "a.png")
        dialog.analyze()
        by_title = {r["title"].text(): r for r in dialog._rows}
        by_title["Bài 1"]["remind"].set_value(60)
        dialog.set_study(by_title["Bài 1"], (datetime(2026, 10, 19, 19, 0), datetime(2026, 10, 19, 21, 0)))
        dialog.accept()
        return True

    window._exec_dialog = run
    window.new_deadline()
    bai1 = next(d for d in repo.items_for_subject("Toán cao cấp", Deadline.KIND) if d.title == "Bài 1")
    assert bai1.remind_before_minutes == 60
    studies = repo.items_for_subject("Toán cao cấp", "study")
    assert len(studies) == 1 and studies[0].deadline_id == bai1.id and studies[0].title == "Bài 1"
    assert (studies[0].starts_at, studies[0].ends_at) == (datetime(2026, 10, 19, 19, 0), datetime(2026, 10, 19, 21, 0))
    assert repo.items_for_subject("Vật lý", "study") == []          # dòng khác không đặt ⇒ không có Study time
