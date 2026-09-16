from __future__ import annotations

from datetime import date, timedelta

from .calendar import github_row, sunday_on_or_before, week_index
from .patterns import Pattern


def render_window(
    pattern: Pattern,
    epoch_sunday: date,
    end: date,
    weeks: int = 53,
    shades: tuple[str, str, str, str, str] = ("  ", "░░", "▒▒", "▓▓", "██"),
    future: str = "··",
) -> str:
    if weeks < 1:
        raise ValueError("weeks must be positive")

    end_sunday = sunday_on_or_before(end)
    start_sunday = end_sunday - timedelta(weeks=weeks - 1)
    rows: list[str] = []

    labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    for row in range(7):
        line = [f"{labels[row]} "]
        for col in range(weeks):
            sunday = start_sunday + timedelta(weeks=col)
            day = sunday + timedelta(days=row)
            if day > end:
                line.append(future)
                continue
            x = week_index(day, epoch_sunday)
            intensity = pattern.intensity(x, github_row(day)) if x >= 0 else 0
            line.append(shades[intensity])
        rows.append("".join(line))
    return "\n".join(rows)
