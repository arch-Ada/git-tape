from __future__ import annotations

from datetime import date

import pytest

from git_tape.config import TapeConfig, levels_from_max_commits
from git_tape.validation import require_int


def test_levels_from_max_commits_are_monotone() -> None:
    levels = levels_from_max_commits(100)
    assert levels == [0, 25, 50, 75, 100]


def test_version_1_config_is_upgraded() -> None:
    cfg = TapeConfig.from_dict(
        {
            "version": 1,
            "epoch_sunday": "2026-01-04",
            "commits_per_pixel": 20,
            "author_name": "Test Author",
            "pattern": {"type": "primes", "gap": 1},
        }
    )
    assert cfg.epoch_sunday == date(2026, 1, 4)
    assert cfg.commits_per_pixel == 20
    assert cfg.commit_levels[-1] == 20


def test_commits_for_intensity_uses_palette() -> None:
    cfg = TapeConfig(
        epoch_sunday=date(2026, 1, 4),
        commit_levels=(0, 10, 20, 50, 100),
        author_name="Test Author",
        pattern={"type": "primes", "gap": 1},
    )
    assert cfg.commits_for_intensity(0) == 0
    assert cfg.commits_for_intensity(4) == 100


@pytest.mark.parametrize(
    "changes",
    [
        {"epoch_sunday": "2026-01-05"},
        {"pattern": {"type": "rule", "rule": 999}},
        {"pattern": {"type": "text", "text": ""}},
        {"commit_levels": [0, 3, 2, 1, 4]},
    ],
)
def test_loaded_config_is_validated(changes) -> None:
    data = {
        "version": 2,
        "epoch_sunday": "2026-01-04",
        "commit_levels": [0, 1, 2, 3, 4],
        "author_name": "Test",
        "pattern": {"type": "primes"},
    }
    data.update(changes)
    with pytest.raises(ValueError):
        TapeConfig.from_dict(data)


def test_pattern_settings_are_immutable_and_detached() -> None:
    from dataclasses import FrozenInstanceError

    source = {"type": "text", "text": "HELLO"}
    cfg = TapeConfig(date(2026, 1, 4), (0, 1, 2, 3, 4), "Test", source)
    source["text"] = "CHANGED"
    assert cfg.pattern.text == "HELLO"
    with pytest.raises(FrozenInstanceError):
        cfg.pattern.text = "CHANGED"
    exported = cfg.as_dict()
    exported["pattern"]["text"] = "CHANGED"
    assert cfg.pattern.text == "HELLO"
    assert TapeConfig.from_dict(cfg.as_dict()) == cfg


def test_text_settings_require_text() -> None:
    with pytest.raises(ValueError, match="non-empty text"):
        TapeConfig(date(2026, 1, 4), (0, 1, 2, 3, 4), "Test", {"type": "text"})


# Exercise every configuration entry point without repeating the full matrix
# of invalid types at each one. The shared validator has its own type cases.
@pytest.mark.parametrize(
    "field, bad",
    [
        ("version", True),
        ("commits_per_pixel", 4.5),
        ("commit_levels", "4"),
        ("rule", 30.9),
        ("seed", False),
        ("gap", None),
        ("message_gap", 4.0),
    ],
)
def test_integer_settings_reject_coercion(bad, field) -> None:
    data = {
        "version": 2,
        "epoch_sunday": "2026-01-04",
        "author_name": "Test",
        "commits_per_pixel": 4,
        "pattern": {"type": "text", "text": "TEST"},
    }
    if field in ("rule", "seed"):
        data["pattern"] = {"type": "rule", field: bad}
    elif field in ("gap", "message_gap"):
        data["pattern"][field] = bad
    elif field == "commit_levels":
        data[field] = [0, 1, 2, 3, bad]
    else:
        data[field] = bad
    with pytest.raises(ValueError, match="integer"):
        TapeConfig.from_dict(data)


@pytest.mark.parametrize("stage", ["flush", "replace"])
def test_failed_config_write_preserves_original(tmp_path, monkeypatch, stage) -> None:
    from git_tape import config

    cfg = TapeConfig(date(2026, 1, 4), (0, 1, 2, 3, 4), "Test", {"type": "primes"})
    config.save_config(tmp_path, cfg)
    path = tmp_path / config.CONFIG_PATH
    before = path.read_bytes()

    def fail(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(config.os, "fsync" if stage == "flush" else "replace", fail)
    changed = TapeConfig(date(2026, 1, 4), (0, 1, 2, 3, 4), "Changed", {"type": "primes"})
    with pytest.raises(OSError):
        config.save_config(tmp_path, changed)
    assert path.read_bytes() == before
    assert config.load_config(tmp_path) == cfg
    assert not list(path.parent.glob(".config-*.tmp"))


def test_atomic_config_write_round_trip(tmp_path) -> None:
    from git_tape.config import load_config, save_config

    for name in ("First", "Updated"):
        cfg = TapeConfig(date(2026, 1, 4), (0, 1, 2, 3, 4), name, {"type": "primes"})
        save_config(tmp_path, cfg)
        assert load_config(tmp_path) == cfg


@pytest.mark.parametrize("value", [True, False, 30.9, 30.0, "30", None])
def test_integer_validation_rejects_non_integer_types(value) -> None:
    with pytest.raises(ValueError, match="integer"):
        require_int(value, "setting")


@pytest.mark.parametrize("changes", [
    {"epoch_sunday": None},
    {"author_name": None},
    {"author_name": " "},
    {"pattern": []},
    {"commit_levels": None},
])
def test_malformed_config_fields_raise_validation_errors(changes) -> None:
    data = {
        "version": 2, "epoch_sunday": "2026-01-04", "author_name": "Test",
        "pattern": {"type": "primes"}, "commit_levels": [0, 1, 2, 3, 4],
    }
    data.update(changes)
    with pytest.raises(ValueError):
        TapeConfig.from_dict(data)
