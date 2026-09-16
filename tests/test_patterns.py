from __future__ import annotations

from git_tape.patterns import ElementaryRulePattern, PrimeTickerPattern, RepeatingTextPattern


def test_text_pattern_is_defined_everywhere_nonnegative() -> None:
    p = RepeatingTextPattern("TEST")
    assert all(isinstance(p.pixel(x, y), bool) for x in range(100) for y in range(7))
    assert all(0 <= p.intensity(x, y) <= 4 for x in range(100) for y in range(7))


def test_prime_ticker_grows_lazily() -> None:
    p = PrimeTickerPattern()
    assert all(isinstance(p.pixel(200, y), bool) for y in range(7))
    assert all(0 <= p.intensity(200, y) <= 4 for y in range(7))


def test_rule_pattern_seed_is_first_column() -> None:
    p = ElementaryRulePattern(rule=30, seed=0b0001000)
    assert p.pixel(0, 3)
    assert p.intensity(0, 3) == 4
    assert sum(p.pixel(0, y) for y in range(7)) == 1


def test_distant_rule_generations_match_iterative_reference() -> None:
    # Cover every rule with two different seeds, including transient states.
    for rule in range(256):
        for seed in (8, 53):
            pattern = ElementaryRulePattern(rule, seed)
            state = seed
            for _ in range(1500):
                cells = [(state >> row) & 1 for row in range(7)]
                state = sum(
                    ((rule >> (4 * cells[(row - 1) % 7] + 2 * cells[row]
                               + cells[(row + 1) % 7])) & 1) << row
                    for row in range(7)
                )
            actual = sum(int(pattern.pixel(1500, row)) << row for row in range(7))
            assert actual == state


def test_preview_with_old_rule_epoch() -> None:
    from datetime import date

    from git_tape.render import render_window

    result = render_window(ElementaryRulePattern(), date(2000, 1, 2), date(2026, 9, 16))
    assert len(result.splitlines()) == 7
