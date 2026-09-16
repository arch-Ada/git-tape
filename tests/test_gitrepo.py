from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from git_tape import gitrepo
from git_tape.gitrepo import (
    LOG_PATH,
    GitError,
    create_pixel_commit,
    existing_pixel_counts,
    init_repo,
)


def test_pixel_commit_does_not_sweep_unrelated_file(tmp_path: Path) -> None:
    init_repo(tmp_path)
    unrelated = tmp_path / "do-not-commit.txt"
    unrelated.write_text("nope\n")
    # Bootstrap commit may include only canvas-owned files; here that means paint.log.
    create_pixel_commit(
        repo=tmp_path,
        day=date(2026, 9, 14),
        ordinal=1,
        total=1,
        author_name="Test",
        author_email="test@example.invalid",
    )
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path, text=True, capture_output=True, check=True
    ).stdout.splitlines()
    assert "do-not-commit.txt" not in tracked
    assert existing_pixel_counts(tmp_path)[date(2026, 9, 14)] == 1


def test_failed_commit_can_be_retried(tmp_path) -> None:
    init_repo(tmp_path)
    hook = tmp_path / ".git/hooks/pre-commit"
    for day in (date(2026, 9, 14), date(2026, 9, 15)):
        args = (tmp_path, day, 1, 1, "Test", "test@example.invalid")
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with pytest.raises(GitError):
            create_pixel_commit(*args)
        assert (
            subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=tmp_path) == b""
        )
        assert not (tmp_path / ".git/index.lock").exists()
        hook.unlink()
        create_pixel_commit(*args)
        assert existing_pixel_counts(tmp_path)[day] == 1
    assert len((tmp_path / LOG_PATH).read_text().splitlines()) == 2


@pytest.mark.parametrize("filename", ["private.txt", "README.md"])
def test_staged_and_unstaged_content_are_preserved(tmp_path, filename) -> None:
    # README is canvas-owned on bootstrap; arbitrary files are not. Neither
    # case may overwrite the user's staged version with their unstaged edits.
    init_repo(tmp_path)
    path = tmp_path / filename
    path.write_text("staged version\n")
    subprocess.run(["git", "add", filename], cwd=tmp_path, check=True)
    path.write_text("unstaged version\n")
    with pytest.raises(GitError, match="staged"):
        create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert (
        subprocess.check_output(["git", "show", f":{filename}"], cwd=tmp_path)
        == b"staged version\n"
    )
    assert path.read_text() == "unstaged version\n"
    assert not (tmp_path / LOG_PATH).exists()


def test_concurrent_staging_cannot_enter_pixel_commit(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    (tmp_path / "coding.txt").write_text("unrelated work\n")
    original = gitrepo._run
    attempts = []

    def run(repo, *args, **kwargs) -> subprocess.CompletedProcess[str]:
        if args[0] == "commit":
            attempts.append(
                subprocess.run(["git", "add", "coding.txt"], cwd=repo, capture_output=True)
            )
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(gitrepo, "_run", run)
    create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert attempts and attempts[0].returncode != 0
    assert b"index.lock" in attempts[0].stderr
    assert "coding.txt" not in subprocess.check_output(
        ["git", "ls-tree", "--name-only", "HEAD"], cwd=tmp_path, text=True
    )
    assert subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=tmp_path) == b""
    assert not (tmp_path / ".git/index.lock").exists()


def test_existing_index_lock_is_preserved(tmp_path) -> None:
    init_repo(tmp_path)
    lock = tmp_path / ".git/index.lock"
    lock.write_text("another operation")
    with pytest.raises(GitError, match="index lock"):
        create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert lock.read_text() == "another operation"
    assert not (tmp_path / LOG_PATH).exists()


def test_empty_repository_has_no_pixels(tmp_path) -> None:
    init_repo(tmp_path)
    assert not existing_pixel_counts(tmp_path)


def test_history_errors_are_not_treated_as_empty(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    original = gitrepo._run

    def fail_log(repo, *args, **kwargs):
        if args[0] == "log":
            raise GitError("history read failed")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(gitrepo, "_run", fail_log)
    with pytest.raises(GitError, match="history read failed"):
        existing_pixel_counts(tmp_path)


def test_corrupt_head_is_not_treated_as_empty(tmp_path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".git/refs/heads/main").write_text("f" * 40 + "\n")
    with pytest.raises(GitError):
        existing_pixel_counts(tmp_path)


@pytest.mark.parametrize(
    "failure, expected",
    [(OSError, gitrepo.IndexPublicationError), (KeyboardInterrupt, KeyboardInterrupt)],
)
def test_failed_index_publication_preserves_recovery_files(
    tmp_path, monkeypatch, failure, expected
) -> None:
    init_repo(tmp_path)

    def fail_replace(*args):
        raise failure("simulated publication failure")

    monkeypatch.setattr(gitrepo.os, "replace", fail_replace)
    with pytest.raises(expected):
        create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert existing_pixel_counts(tmp_path)[date(2026, 9, 14)] == 1
    assert (tmp_path / ".git/index.lock").exists()
    retained = list((tmp_path / ".git").glob("git-tape-index-*/index"))
    assert len(retained) == 1
    assert retained[0].stat().st_size > 0
    assert (retained[0].parent / "commit-pending").exists()


@pytest.mark.parametrize("variable", gitrepo.GIT_ROUTING_VARIABLES)
def test_inherited_git_routing_is_rejected(tmp_path, monkeypatch, variable) -> None:
    requested = tmp_path / "requested"
    other = tmp_path / "other"
    init_repo(requested)
    init_repo(other)
    monkeypatch.setenv(variable, str(other / ".git"))
    with pytest.raises(GitError, match="repository-routing"):
        create_pixel_commit(requested, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert not (requested / LOG_PATH).exists()
    assert not (other / LOG_PATH).exists()
    assert not (other / ".git/refs/heads/main").exists()
    assert not (requested / ".git/index.lock").exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, RuntimeError, GitError])
def test_exception_after_commit_preserves_recovery(tmp_path, monkeypatch, failure) -> None:
    init_repo(tmp_path)
    original = gitrepo._run

    def run(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if args[0] == "commit":
            raise failure("interrupted after commit")
        return result

    monkeypatch.setattr(gitrepo, "_run", run)
    with pytest.raises(failure) as error:
        create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert "Retained private index" in "\n".join(error.value.__notes__)
    assert existing_pixel_counts(tmp_path)[date(2026, 9, 14)] == 1
    retained = list((tmp_path / ".git").glob("git-tape-index-*/index"))
    assert len(retained) == 1
    assert (retained[0].parent / "commit-pending").exists()
    assert (tmp_path / ".git/index.lock").exists()
    # Verify the retained index matches the committed tree and is recoverable.
    import os

    env = dict(os.environ, GIT_INDEX_FILE=str(retained[0]))
    assert (
        subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=tmp_path, env=env)
        == b""
    )


def test_session_requires_context_and_cannot_be_reused(tmp_path) -> None:
    init_repo(tmp_path)
    unopened = gitrepo.PaintingSession(tmp_path)
    with pytest.raises(GitError, match="inactive"):
        unopened.existing_pixel_counts()
    with pytest.raises(GitError, match="inactive"):
        unopened.create_pixel_commit(date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    with gitrepo.painting_session(tmp_path) as session:
        session.create_pixel_commit(date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
        assert session.existing_pixel_counts()[date(2026, 9, 14)] == 1
    with pytest.raises(GitError, match="inactive"):
        session.existing_pixel_counts()
    with pytest.raises(GitError, match="inactive"):
        session.create_pixel_commit(date(2026, 9, 15), 1, 1, "Test", "test@example.invalid")
    assert existing_pixel_counts(tmp_path) == {date(2026, 9, 14): 1}


def test_caught_commit_failure_invalidates_session(tmp_path) -> None:
    init_repo(tmp_path)
    hook = tmp_path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with gitrepo.painting_session(tmp_path) as session:
        with pytest.raises(GitError):
            session.create_pixel_commit(date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
        hook.unlink()
        with pytest.raises(GitError, match="failed"):
            session.create_pixel_commit(date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert not (tmp_path / ".git/index.lock").exists()
    create_pixel_commit(tmp_path, date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
    assert existing_pixel_counts(tmp_path)[date(2026, 9, 14)] == 1


def test_caught_interruption_still_preserves_recovery(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    original = gitrepo._run

    def run(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if args[0] == "commit":
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(gitrepo, "_run", run)
    with gitrepo.painting_session(tmp_path) as session:
        with pytest.raises(KeyboardInterrupt):
            session.create_pixel_commit(date(2026, 9, 14), 1, 1, "Test", "test@example.invalid")
        with pytest.raises(GitError, match="inactive"):
            session.existing_pixel_counts()
    assert (tmp_path / ".git/index.lock").exists()
    assert list((tmp_path / ".git").glob("git-tape-index-*/commit-pending"))
