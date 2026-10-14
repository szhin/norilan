from __future__ import annotations

from datetime import datetime

from app.colors import (
    AUTO_TONES,
    ColorResolver,
    accent_color,
    accent_tokens,
    assign_missing_tones,
    auto_tone,
    is_hex,
    mix,
    pick_tone,
)
from app.models import Event, Task

DUE = datetime(2026, 10, 15, 9)


def test_auto_tone_is_stable_and_ignores_accents_and_case():
    assert auto_tone("Toán cao cấp") == auto_tone("TOAN CAO CAP") == auto_tone("  toán cao cấp ")
    assert auto_tone("Toán") in AUTO_TONES
    assert auto_tone("") == "gray"


def test_pick_tone_prefers_least_used():
    assert pick_tone([]) == AUTO_TONES[0]
    assert pick_tone(["blue"]) == "green"
    assert pick_tone(list(AUTO_TONES)) == AUTO_TONES[0]  # dùng hết một vòng → quay lại đầu


def test_assign_missing_tones_gives_distinct_colors():
    subjects = [f"Môn {i}" for i in range(7)]
    assigned = assign_missing_tones(subjects, {})
    assert len(set(assigned.values())) == 7
    assert assign_missing_tones(["A", "B"], {"A": "red"}) == {"B": "blue"}
    assert assign_missing_tones(["", "  "], {}) == {}


def test_resolver_modes():
    task = Task("A", DUE, subject="Toán")
    no_subject = Task("B", DUE)
    event = Event("E", DUE, DUE, color="green")
    plain_event = Event("F", DUE, DUE)
    by_subject = ColorResolver("subject", {"Toán": "purple"})
    assert by_subject.tone(task) == "purple"
    assert by_subject.tone(no_subject) == "gray"
    assert by_subject.tone(event) == "green" and by_subject.tone(plain_event) == "orange"
    by_type = ColorResolver("type", {"Toán": "purple"})
    assert by_type.tone(task) == "blue" and by_type.tone(event) == "green"
    assert ColorResolver("subject", {"Toán": "not-a-tone"}).tone(task) == auto_tone("Toán")


def test_accent_helpers():
    assert is_hex("#12ab34") and not is_hex("12ab34") and not is_hex("#12ab3")
    assert mix("#000000", "#ffffff", 0.5) == "#808080"
    assert accent_color("purple", dark=False) != accent_color("purple", dark=True)
    assert accent_color("#123456", dark=True) == "#123456"
    assert accent_color("khong-co", dark=False) == accent_color("blue", dark=False)
    tokens = accent_tokens("green", dark=False, bg="#ffffff")
    assert set(tokens) == {"accent", "accent_hover", "selected_cell"}
