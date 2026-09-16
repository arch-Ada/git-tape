from __future__ import annotations

from datetime import date
from pathlib import Path

from git_tape.config import TapeConfig
from git_tape.gitrepo import existing_pixel_counts, init_repo
from git_tape.planner import paint


def _lit_rule_config(target: int = 4) -> TapeConfig:
    return TapeConfig(
        epoch_sunday=date(2026, 9, 13),
        commit_levels=(0, 1, 2, 3, target),
        author_name="Test",
        # Rule 255 makes every cell lit after the first generation; use x=1 below.
        pattern={"type": "rule", "rule": 255, "seed": 0b1111111},
    )


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    init_repo(tmp_path)
    day = date(2026, 9, 14)
    config = _lit_rule_config(target=4)

    first = paint(tmp_path, config, "test@example.invalid", day, day, allow_backfill=True)
    second = paint(tmp_path, config, "test@example.invalid", day, day, allow_backfill=True)

    assert first.created == 4
    assert second.created == 0
    assert existing_pixel_counts(tmp_path)[day] == 4


def test_increasing_target_only_tops_up_difference(tmp_path: Path) -> None:
    init_repo(tmp_path)
    day = date(2026, 9, 14)

    paint(
        tmp_path, _lit_rule_config(target=4), "test@example.invalid", day, day, allow_backfill=True
    )
    result = paint(
        tmp_path, _lit_rule_config(target=7), "test@example.invalid", day, day, allow_backfill=True
    )

    assert result.created == 3
    assert existing_pixel_counts(tmp_path)[day] == 7


def test_paint_holds_lock_between_commits(tmp_path, monkeypatch) -> None:
    import pytest

    from git_tape.gitrepo import GitError, PaintingSession

    init_repo(tmp_path)
    original_commit = PaintingSession.create_pixel_commit
    attempts = []
    config = _lit_rule_config()
    day = date(2026, 9, 14)

    def commit(*args, **kwargs):
        original_commit(*args, **kwargs)
        with pytest.raises(GitError, match="index lock"):
            paint(tmp_path, config, "test@example.invalid", day, day)
        attempts.append(1)

    monkeypatch.setattr(PaintingSession, "create_pixel_commit", commit)
    assert paint(tmp_path, config, "test@example.invalid", day, day).created == 4
    assert len(attempts) == 4


def test_failure_after_successful_commit_can_resume(tmp_path, monkeypatch) -> None:
    import subprocess

    import pytest

    from git_tape import gitrepo
    from git_tape.gitrepo import GitError

    init_repo(tmp_path)
    original = gitrepo._run
    commits = []

    def run(repo, *args, **kwargs):
        if args[0] == "commit":
            commits.append(1)
            if len(commits) == 2:
                raise GitError("simulated failed second commit")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(gitrepo, "_run", run)
    day = date(2026, 9, 14)
    config = _lit_rule_config()
    with pytest.raises(GitError, match="second commit"):
        paint(tmp_path, config, "test@example.invalid", day, day)
    assert existing_pixel_counts(tmp_path)[day] == 1
    assert subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=tmp_path) == b""
    assert not (tmp_path / ".git/index.lock").exists()
    assert paint(tmp_path, config, "test@example.invalid", day, day).created == 3
    assert existing_pixel_counts(tmp_path)[day] == 4
