from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .patterns import PatternConfig, parse_pattern_config, pattern_config_dict, pattern_from_config
from .validation import require_int

CONFIG_PATH = Path(".git-tape/config.json")
CONFIG_VERSION = 2
INTENSITY_LEVELS = 5


def levels_from_max_commits(max_commits: int) -> list[int]:
    require_int(max_commits, "commits-per-pixel")
    if max_commits < 1:
        raise ValueError("commits-per-pixel must be positive")
    levels = [0]
    for step in range(1, INTENSITY_LEVELS):
        if step == INTENSITY_LEVELS - 1:
            levels.append(max_commits)
        else:
            levels.append(max(1, round(max_commits * step / (INTENSITY_LEVELS - 1))))
    return normalize_commit_levels(levels)


def normalize_commit_levels(levels: list[int] | tuple[int, ...]) -> list[int]:
    values = [require_int(level, "commit level") for level in levels]
    if len(values) != INTENSITY_LEVELS:
        raise ValueError(f"commit levels must contain exactly {INTENSITY_LEVELS} integers")
    if values[0] != 0:
        raise ValueError("the first commit level must be 0 for unlit pixels")
    if any(level < 0 for level in values):
        raise ValueError("commit levels must be non-negative")
    if any(right < left for left, right in zip(values, values[1:])):
        raise ValueError("commit levels must be non-decreasing")
    return values


@dataclass(frozen=True, init=False)
class TapeConfig:
    epoch_sunday: date
    commit_levels: tuple[int, int, int, int, int]
    author_name: str
    pattern: PatternConfig

    def __init__(
        self,
        epoch_sunday: date,
        commit_levels: tuple[int, int, int, int, int],
        author_name: str,
        pattern: PatternConfig | Mapping[str, Any],
    ) -> None:
        object.__setattr__(self, "epoch_sunday", epoch_sunday)
        object.__setattr__(self, "author_name", author_name)
        object.__setattr__(self, "pattern", parse_pattern_config(pattern))
        object.__setattr__(self, "commit_levels", tuple(normalize_commit_levels(commit_levels)))
        self.validate()

    def validate(self) -> None:
        """Validate settings regardless of whether they came from the CLI or JSON."""
        if self.epoch_sunday.weekday() != 6:
            raise ValueError("epoch must be a Sunday")
        normalize_commit_levels(self.commit_levels)
        pattern_from_config(self.pattern)

    @property
    def commits_per_pixel(self) -> int:
        return self.commit_levels[-1]

    def commits_for_intensity(self, intensity: int) -> int:
        if not 0 <= intensity < INTENSITY_LEVELS:
            raise ValueError(f"intensity must be in 0..{INTENSITY_LEVELS - 1}")
        return self.commit_levels[intensity]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "epoch_sunday": self.epoch_sunday.isoformat(),
            "commit_levels": list(self.commit_levels),
            "commits_per_pixel": self.commits_per_pixel,
            "author_name": self.author_name,
            "pattern": pattern_config_dict(self.pattern),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TapeConfig":
        if not isinstance(data, dict):
            raise ValueError("config must be a JSON object")
        for field in ("epoch_sunday", "author_name", "pattern"):
            if field not in data:
                raise ValueError(f"config needs {field}")
        if not isinstance(data["epoch_sunday"], str):
            raise ValueError("epoch_sunday must be a date string (YYYY-MM-DD)")
        if not isinstance(data["author_name"], str) or not data["author_name"].strip():
            raise ValueError("author_name must be a non-empty string")
        if not isinstance(data["pattern"], dict):
            raise ValueError("pattern must be a JSON object")
        version = require_int(data.get("version", 1), "version")
        if version == 1:
            if "commits_per_pixel" not in data:
                raise ValueError("config needs commits_per_pixel")
            max_commits = require_int(data["commits_per_pixel"], "commits-per-pixel")
            commit_levels = levels_from_max_commits(max_commits)
        elif version == CONFIG_VERSION:
            if "commit_levels" in data:
                if not isinstance(data["commit_levels"], (list, tuple)):
                    raise ValueError("commit_levels must be a list of integers")
                commit_levels = normalize_commit_levels(data["commit_levels"])
            elif "commits_per_pixel" in data:
                commit_levels = levels_from_max_commits(
                    require_int(data["commits_per_pixel"], "commits-per-pixel")
                )
            else:
                raise ValueError("config needs commit_levels or commits_per_pixel")
        else:
            raise ValueError("unsupported config version")

        return cls(
            epoch_sunday=date.fromisoformat(data["epoch_sunday"]),
            commit_levels=tuple(commit_levels),
            author_name=str(data["author_name"]),
            pattern=parse_pattern_config(data["pattern"]),
        )


def save_config(repo: Path, config: TapeConfig) -> None:
    config.validate()
    path = repo / CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.as_dict(), indent=2) + "\n"
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".config-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_config(repo: Path) -> TapeConfig:
    path = repo / CONFIG_PATH
    try:
        payload = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(
            f"no canvas configuration at {path}; initialize a dedicated canvas with git-tape init"
        ) from exc
    try:
        return TapeConfig.from_dict(json.loads(payload))
    except ValueError as exc:
        raise ValueError(f"invalid canvas configuration at {path}: {exc}") from exc
