from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .calendar import pixels_between
from .config import TapeConfig
from .gitrepo import painting_session
from .patterns import pattern_from_config


@dataclass(frozen=True)
class PaintResult:
    created: int
    lit_days: int
    skipped_existing: int


def paint(
    repo: Path,
    config: TapeConfig,
    author_email: str,
    start: date,
    end: date,
    allow_backfill: bool = False,
) -> PaintResult:
    with painting_session(repo) as session:
        pattern = pattern_from_config(config.pattern)
        existing = session.existing_pixel_counts()
        latest = max(existing, default=None)

        targets = [p for p in pixels_between(pattern, config.epoch_sunday, start, end) if p.on]
        if latest and not allow_backfill:
            missing_old = [
                p.day
                for p in targets
                if p.day < latest and existing[p.day] < config.commits_for_intensity(p.intensity)
            ]
            if missing_old:
                first = min(missing_old)
                raise ValueError(
                    f"would backfill {first} behind existing history ending {latest}; "
                    "rerun with --allow-backfill if that is intentional"
                )

        created = 0
        skipped = 0
        for pixel in targets:
            have = existing[pixel.day]
            target = config.commits_for_intensity(pixel.intensity)
            need = max(0, target - have)
            skipped += min(have, target)
            for i in range(need):
                ordinal = have + i + 1
                session.create_pixel_commit(
                    day=pixel.day,
                    ordinal=ordinal,
                    total=target,
                    author_name=config.author_name,
                    author_email=author_email,
                )
                created += 1

        return PaintResult(created=created, lit_days=len(targets), skipped_existing=skipped)
