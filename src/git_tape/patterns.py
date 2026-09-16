from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from functools import cached_property
from math import isqrt
from typing import Any, Literal, Protocol, TypeAlias

from .font import glyph
from .validation import require_int

HEIGHT = 7
MAX_INTENSITY = 4


@dataclass(frozen=True)
class TextConfig:
    text: str
    gap: int = 1
    message_gap: int = 4
    type: Literal["text"] = field(default="text", init=False)


@dataclass(frozen=True)
class PrimeConfig:
    gap: int = 1
    type: Literal["primes"] = field(default="primes", init=False)


@dataclass(frozen=True)
class RuleConfig:
    rule: int = 30
    seed: int = 0b0001000
    type: Literal["rule"] = field(default="rule", init=False)


PatternConfig: TypeAlias = TextConfig | PrimeConfig | RuleConfig


def parse_pattern_config(config: Mapping[str, Any] | PatternConfig) -> PatternConfig:
    if isinstance(config, (TextConfig, PrimeConfig, RuleConfig)):
        settings = config
    else:
        kind = config.get("type")
        if kind == "text":
            text = config.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("text pattern needs non-empty text")
            settings = TextConfig(
                text,
                require_int(config.get("gap", 1), "gap"),
                require_int(config.get("message_gap", 4), "message_gap"),
            )
        elif kind == "primes":
            settings = PrimeConfig(require_int(config.get("gap", 1), "gap"))
        elif kind == "rule":
            settings = RuleConfig(
                require_int(config.get("rule", 30), "rule"),
                require_int(config.get("seed", 0b0001000), "seed"),
            )
        else:
            raise ValueError(f"unknown pattern type: {kind}")
    pattern_from_config(settings)
    return settings


def pattern_config_dict(config: PatternConfig) -> dict[str, str | int]:
    return asdict(config)


class Pattern(Protocol):
    """An infinite 7-row bitmap addressed by (week_index, weekday_row)."""

    def intensity(self, x: int, y: int) -> int: ...

    def pixel(self, x: int, y: int) -> bool: ...


def _text_columns(text: str, gap: int = 1) -> list[tuple[bool, ...]]:
    """Render text into 7-high columns with one blank row above/below."""
    columns: list[tuple[bool, ...]] = []
    for index, char in enumerate(text):
        rows = glyph(char)
        for col in range(3):
            columns.append((False, *(row[col] == "1" for row in rows), False))
        if index != len(text) - 1:
            columns.extend([(False,) * HEIGHT] * gap)
    return columns


@dataclass(frozen=True)
class RepeatingTextPattern:
    text: str
    gap: int = 2
    message_gap: int = 4

    def __post_init__(self) -> None:
        require_int(self.gap, "gap")
        require_int(self.message_gap, "message_gap")
        if self.gap < 0 or self.message_gap < 0:
            raise ValueError("text gaps must be non-negative")
        if not self.text:
            raise ValueError("text pattern needs non-empty text")

    @cached_property
    def _cycle(self) -> tuple[tuple[bool, ...], ...]:
        return tuple(_text_columns(self.text, self.gap) + [(False,) * HEIGHT] * self.message_gap)

    def intensity(self, x: int, y: int) -> int:
        cycle = self._cycle
        return MAX_INTENSITY if cycle[x % len(cycle)][y] else 0

    def pixel(self, x: int, y: int) -> bool:
        return self.intensity(x, y) > 0


class _PrimeStream:
    """Lazily grows the textual stream '2 3 5 7 11 13 ...'."""

    def __init__(self) -> None:
        self._primes: list[int] = []
        self._candidate = 2
        self.text = ""

    def ensure_chars(self, minimum: int) -> None:
        while len(self.text) < minimum:
            n = self._candidate
            self._candidate += 1
            if self._is_prime(n):
                self._primes.append(n)
                self.text += f"{n} "

    def _is_prime(self, n: int) -> bool:
        if n < 2:
            return False
        limit = isqrt(n)
        for p in self._primes:
            if p > limit:
                break
            if n % p == 0:
                return False
        return True


@dataclass
class PrimeTickerPattern:
    """An unbounded scrolling stream of prime numbers in a tiny 3x5 font."""

    gap: int = 1

    def __post_init__(self) -> None:
        require_int(self.gap, "gap")
        if self.gap < 0:
            raise ValueError("prime gap must be non-negative")
        self._stream = _PrimeStream()
        self._columns: list[tuple[bool, ...]] = []
        self._rendered_chars = 0

    def _ensure_column(self, x: int) -> None:
        while len(self._columns) <= x:
            self._stream.ensure_chars(self._rendered_chars + 16)
            fresh = self._stream.text[self._rendered_chars :]
            if not fresh:
                continue
            for char in fresh:
                rows = glyph(char)
                for col in range(3):
                    self._columns.append((False, *(row[col] == "1" for row in rows), False))
                self._columns.extend([(False,) * HEIGHT] * self.gap)
            self._rendered_chars = len(self._stream.text)

    def intensity(self, x: int, y: int) -> int:
        if x < 0:
            return 0
        self._ensure_column(x)
        return MAX_INTENSITY if self._columns[x][y] else 0

    def pixel(self, x: int, y: int) -> bool:
        return self.intensity(x, y) > 0


@dataclass(frozen=True)
class ElementaryRulePattern:
    """A 7-cell elementary cellular automaton advancing one generation per week.

    The vertical edge wraps, so each week is exactly one GitHub column. With only
    seven cells the state space is finite, hence eventually periodic, but the tape
    is defined for every non-negative week index.
    """

    rule: int = 30
    seed: int = 0b0001000

    def __post_init__(self) -> None:
        require_int(self.rule, "rule")
        require_int(self.seed, "seed")
        if not 0 <= self.rule <= 255:
            raise ValueError("rule must be in 0..255")
        if not 0 <= self.seed < (1 << HEIGHT):
            raise ValueError("seed must fit in 7 bits")

    def _next(self, state: int) -> int:
        out = 0
        for y in range(HEIGHT):
            left = (state >> ((y - 1) % HEIGHT)) & 1
            center = (state >> y) & 1
            right = (state >> ((y + 1) % HEIGHT)) & 1
            neighborhood = (left << 2) | (center << 1) | right
            bit = (self.rule >> neighborhood) & 1
            out |= bit << y
        return out

    @cached_property
    def _orbit(self) -> tuple[tuple[int, ...], int]:
        # Seven cells give at most 128 states. Store the transient prefix and
        # one full cycle so distant generations need no recursion or growth.
        states: list[int] = []
        seen: dict[int, int] = {}
        state = self.seed
        while state not in seen:
            seen[state] = len(states)
            states.append(state)
            state = self._next(state)
        return tuple(states), seen[state]

    def _state_at(self, x: int) -> int:
        if x <= 0:
            return self.seed
        states, cycle_start = self._orbit
        if x >= len(states):
            x = cycle_start + (x - cycle_start) % (len(states) - cycle_start)
        return states[x]

    def intensity(self, x: int, y: int) -> int:
        if x < 0:
            return 0
        return MAX_INTENSITY if ((self._state_at(x) >> y) & 1) else 0

    def pixel(self, x: int, y: int) -> bool:
        return self.intensity(x, y) > 0


def pattern_from_config(config: PatternConfig) -> Pattern:
    if isinstance(config, TextConfig):
        return RepeatingTextPattern(config.text, config.gap, config.message_gap)
    if isinstance(config, PrimeConfig):
        return PrimeTickerPattern(config.gap)
    if isinstance(config, RuleConfig):
        return ElementaryRulePattern(config.rule, config.seed)
    raise ValueError("expected validated pattern settings")
