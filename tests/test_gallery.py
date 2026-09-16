from __future__ import annotations

import pytest

from git_tape.gallery import render_rule_gallery, render_rule_preview


def test_rule_preview_is_seven_cells_high_plus_header() -> None:
    preview = render_rule_preview(30, weeks=12)
    assert len(preview) == 8
    assert "Rule 030" in preview[0]
    assert all(len(line) == 12 for line in preview)


def test_gallery_contains_requested_rules_only() -> None:
    gallery = render_rule_gallery(start_rule=30, end_rule=33, weeks=12, columns=2)
    assert "Rule 030" in gallery
    assert "Rule 033" in gallery
    assert "Rule 029" not in gallery
    assert "Rule 034" not in gallery


def test_gallery_is_deterministic() -> None:
    a = render_rule_gallery(start_rule=90, end_rule=95, weeks=10, columns=3, seed=0b0001000)
    b = render_rule_gallery(start_rule=90, end_rule=95, weeks=10, columns=3, seed=0b0001000)
    assert a == b


def test_gallery_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        render_rule_gallery(start_rule=10, end_rule=9)
