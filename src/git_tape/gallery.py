from __future__ import annotations

from .patterns import HEIGHT, ElementaryRulePattern


def render_rule_preview(
    rule: int,
    *,
    weeks: int = 16,
    seed: int = 0b0001000,
    on: str = "█",
    off: str = "·",
) -> list[str]:
    if weeks < 1:
        raise ValueError("weeks must be positive")

    pattern = ElementaryRulePattern(rule=rule, seed=seed)
    lines = [f"Rule {rule:03d}".center(weeks)]
    for y in range(HEIGHT):
        lines.append("".join(on if pattern.pixel(x, y) else off for x in range(weeks)))
    return lines


def render_rule_gallery(
    *,
    start_rule: int = 0,
    end_rule: int = 255,
    weeks: int = 16,
    columns: int = 4,
    seed: int = 0b0001000,
    gap: str = "    ",
) -> str:
    if not 0 <= start_rule <= 255:
        raise ValueError("start rule must be in 0..255")
    if not 0 <= end_rule <= 255:
        raise ValueError("end rule must be in 0..255")
    if end_rule < start_rule:
        raise ValueError("end rule must not be before start rule")
    if columns < 1:
        raise ValueError("columns must be positive")
    if weeks < 3:
        raise ValueError("weeks must be at least 3")

    rules = list(range(start_rule, end_rule + 1))
    blocks: list[str] = []
    for offset in range(0, len(rules), columns):
        batch = rules[offset : offset + columns]
        previews = [render_rule_preview(rule, weeks=weeks, seed=seed) for rule in batch]
        for line_index in range(HEIGHT + 1):
            blocks.append(gap.join(preview[line_index] for preview in previews))
        if offset + columns < len(rules):
            blocks.append("")

    return "\n".join(blocks)
