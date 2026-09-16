from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .patterns import Pattern


def sunday_on_or_before(day: date) -> date:
    # Python: Monday=0..Sunday=6. GitHub rows: Sunday=0..Saturday=6.
    days_since_sunday = (day.weekday() + 1) % 7
    return day - timedelta(days=days_since_sunday)


def github_row(day: date) -> int:
    return (day.weekday() + 1) % 7


def week_index(day: date, epoch_sunday: date) -> int:
    if github_row(epoch_sunday) != 0:
        raise ValueError("epoch must be a Sunday")
    return (sunday_on_or_before(day) - epoch_sunday).days // 7


@dataclass(frozen=True)
class Pixel:
    day: date
    week: int
    row: int
    intensity: int
    on: bool


def pixels_between(
    pattern: Pattern,
    epoch_sunday: date,
    start: date,
    end: date,
) -> list[Pixel]:
    if end < start:
        raise ValueError("end must not be before start")
    pixels: list[Pixel] = []
    day = start
    while day <= end:
        w = week_index(day, epoch_sunday)
        row = github_row(day)
        intensity = pattern.intensity(w, row) if w >= 0 else 0
        pixels.append(Pixel(day=day, week=w, row=row, intensity=intensity, on=(intensity > 0)))
        day += timedelta(days=1)
    return pixels
