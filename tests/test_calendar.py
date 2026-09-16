from __future__ import annotations

from datetime import date

from git_tape.calendar import github_row, sunday_on_or_before, week_index


def test_github_rows_are_sunday_first() -> None:
    assert github_row(date(2026, 9, 13)) == 0
    assert github_row(date(2026, 9, 14)) == 1
    assert github_row(date(2026, 9, 19)) == 6


def test_week_index() -> None:
    epoch = date(2026, 9, 13)
    assert week_index(date(2026, 9, 13), epoch) == 0
    assert week_index(date(2026, 9, 19), epoch) == 0
    assert week_index(date(2026, 9, 20), epoch) == 1


def test_sunday_on_or_before() -> None:
    assert sunday_on_or_before(date(2026, 9, 14)) == date(2026, 9, 13)
