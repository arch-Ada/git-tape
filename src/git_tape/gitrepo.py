from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from tempfile import mkdtemp

PREFIX = "[git-tape] pixel "
LOG_PATH = Path(".git-tape/paint.log")
# These inherited settings can override the repository or storage selected by cwd.
GIT_ROUTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
)


class GitError(RuntimeError):
    pass


class IndexPublicationError(GitError):
    """A commit succeeded but its index must be recovered manually."""


def _run(
    repo: Path, *args: str, env: dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    inherited = [name for name in GIT_ROUTING_VARIABLES if name in os.environ]
    if inherited:
        raise GitError(
            "unset repository-routing Git variables before running git-tape: "
            + ", ".join(inherited)
        )
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _run(repo, "init", "-b", "main")


def ensure_repo(repo: Path) -> None:
    result = _run(repo, "rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise GitError(f"not a git repository: {repo}")


def local_config(repo: Path, key: str) -> str | None:
    result = _run(repo, "config", "--local", "--get", key, check=False)
    value = result.stdout.strip()
    return value or None


def configured_author_name(repo: Path) -> str | None:
    """Read the canvas identity, falling back to Git's user/system settings.

    For a new canvas, avoid inheriting the containing repository's local name.
    """
    directory = repo if (repo / ".git").exists() else Path(repo.resolve().anchor)
    result = _run(directory, "config", "--get", "user.name", check=False)
    return result.stdout.strip() or None


def set_local_config(repo: Path, key: str, value: str) -> None:
    _run(repo, "config", "--local", key, value)


def configured_author_email(repo: Path) -> str | None:
    """Read the effective Git email, including global and system settings."""
    result = _run(repo, "config", "--get", "user.email", check=False)
    return result.stdout.strip() or None


def _has_head(repo: Path) -> bool:
    return _run(repo, "rev-parse", "--verify", "HEAD", check=False).returncode == 0


def _ensure_clean_index(repo: Path) -> None:
    result = _run(repo, "diff", "--cached", "--name-only", "-z")
    staged = set(result.stdout.rstrip("\0").split("\0")) - {""}
    if staged:
        raise GitError("refusing to paint while unrelated changes are staged")


def existing_pixel_counts(repo: Path) -> Counter[date]:
    ensure_repo(repo)
    counts: Counter[date] = Counter()
    # Only an unborn symbolic HEAD is empty history. Invalid objects and failed
    # history reads must stop painting rather than look like missing pixels.
    head = _run(repo, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode != 0:
        branch = _run(repo, "symbolic-ref", "-q", "HEAD")
        ref = _run(repo, "show-ref", "--verify", "--quiet", branch.stdout.strip(), check=False)
        if ref.returncode == 1:
            return counts
        raise GitError(head.stderr.strip() or "could not resolve HEAD")
    result = _run(repo, "log", "--format=%aI%x09%s")
    for line in result.stdout.splitlines():
        try:
            iso, subject = line.split("\t", 1)
        except ValueError:
            continue
        if not subject.startswith(PREFIX):
            continue
        counts[date.fromisoformat(iso[:10])] += 1
    return counts


def latest_pixel_date(repo: Path) -> date | None:
    counts = existing_pixel_counts(repo)
    return max(counts) if counts else None


@contextmanager
def painting_session(repo: Path) -> Iterator[PaintingSession]:
    """Hold Git's index lock for one complete painting operation.

    Failed commits never leave generated files staged in the user's index.
    Ordinary Git staging, commits, and checkouts refuse to race this operation.
    """
    index = Path(
        _run(repo, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip()
    )
    lock = Path(str(index) + ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise GitError(
            "another Git operation holds the index lock; retry when it finishes"
        ) from exc
    os.close(fd)
    directory = None
    session = PaintingSession(repo)
    preserve_recovery = False
    try:
        _ensure_clean_index(repo)
        directory = Path(mkdtemp(prefix="git-tape-index-", dir=index.parent))
        private = directory / "index"
        if index.exists():
            private.write_bytes(index.read_bytes())
        session._index = index
        session._private_index = private
        session._active = True
        yield session
    except BaseException as exc:
        if isinstance(exc, IndexPublicationError) or (
            directory is not None and (directory / "commit-pending").exists()
        ):
            preserve_recovery = True
            exc.add_note(
                f"Git commit outcome may require recovery. Retained private index in {directory} "
                f"and lock at {lock}; inspect HEAD before restoring the index and removing the lock."
            )
        raise
    finally:
        session._active = False
        # Preserve uncertain commits even if a caller caught the exception
        # inside the context instead of letting it reach this manager.
        if directory is not None and (directory / "commit-pending").exists():
            preserve_recovery = True
        if not preserve_recovery:
            if directory is not None:
                shutil.rmtree(directory)
            lock.unlink()


class PaintingSession:
    """Repository-bound writer obtained through ``painting_session``.

    Index paths, environment overrides, and lock ownership stay in this module.
    An inactive or failed session cannot read history or create further commits.
    """

    def __init__(self, repo: Path) -> None:
        self._repo = repo.resolve()
        self._index: Path | None = None
        self._private_index: Path | None = None
        self._active = False

    def _require_active(self) -> None:
        if not self._active or self._index is None or self._private_index is None:
            raise GitError("painting session is inactive or has failed")

    def existing_pixel_counts(self) -> Counter[date]:
        self._require_active()
        return existing_pixel_counts(self._repo)

    def create_pixel_commit(
        self,
        day: date,
        ordinal: int,
        total: int,
        author_name: str,
        author_email: str,
    ) -> None:
        self._require_active()
        try:
            self._commit(day, ordinal, total, author_name, author_email)
        except BaseException:
            self._active = False
            raise

    def _commit(
        self,
        day: date,
        ordinal: int,
        total: int,
        author_name: str,
        author_email: str,
    ) -> None:
        repo = self._repo
        private = self._private_index
        index = self._index
        assert private is not None and index is not None
        index_env = {"GIT_INDEX_FILE": str(private)}
        owned = [str(LOG_PATH)]
        if not _has_head(repo):
            for candidate in (Path(".git-tape/config.json"), Path("README.md")):
                if (repo / candidate).exists():
                    owned.append(str(candidate))

        log_path = repo / LOG_PATH
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"{day.isoformat()} {ordinal}/{total}\n"
        # A failed commit may have already appended this pixel to the worktree log.
        encoded = entry.encode("utf-8")
        previous = b""
        if log_path.exists():
            with log_path.open("rb") as handle:
                handle.seek(max(0, log_path.stat().st_size - len(encoded)))
                previous = handle.read()
        with log_path.open("a", encoding="utf-8") as handle:
            if previous != encoded:
                handle.write(entry)
        _run(repo, "add", *owned, env=index_env)

        timestamp = f"{day.isoformat()}T12:00:00+0000"
        env = {
            **index_env,
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_COMMITTER_DATE": timestamp,
        }
        before = _run(repo, "rev-parse", "--verify", "HEAD", check=False).stdout.strip()
        pending = private.parent / "commit-pending"
        pending.write_text(before + "\n", encoding="ascii")
        try:
            _run(repo, "commit", "-m", f"{PREFIX}{day.isoformat()} {ordinal}/{total}", env=env)
        except GitError:
            # A normal rejected commit is retryable only if HEAD is unchanged.
            after = _run(repo, "rev-parse", "--verify", "HEAD", check=False).stdout.strip()
            if after == before:
                pending.unlink()
            raise
        # Keep the pending marker through publication, including interruptions.
        published = private.parent / "published-index"
        try:
            shutil.copyfile(private, published)
            os.replace(published, index)
        except OSError as exc:
            raise IndexPublicationError(
                f"commit succeeded but index publication failed; retained index at {private} "
                f"and lock at {index}.lock. Inspect HEAD and restore the retained index "
                "before removing the lock and retrying."
            ) from exc
        pending.unlink()


def create_pixel_commit(
    repo: Path,
    day: date,
    ordinal: int,
    total: int,
    author_name: str,
    author_email: str,
) -> None:
    """Create one pixel with the same locking and recovery as a full paint run."""
    with painting_session(repo) as session:
        session.create_pixel_commit(day, ordinal, total, author_name, author_email)
