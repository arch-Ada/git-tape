from __future__ import annotations

import subprocess

import pytest

from git_tape.cli import build_parser
from git_tape.config import load_config
from git_tape.gitrepo import init_repo


@pytest.mark.parametrize("command", ["daily", "sync", "install-systemd"])
def test_automation_commands_are_unavailable(command) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([command, "/tmp/canvas"])
    assert exc.value.code == 2


def test_manual_paint_remains_available() -> None:
    args = build_parser().parse_args(["paint", "/tmp/canvas"])
    assert args.command == "paint"
    assert not hasattr(args, "push")


def _run(*argv) -> None:
    args = build_parser().parse_args(list(argv))
    args.func(args)


@pytest.mark.parametrize(
    "options",
    [
        ["--pattern", "rule", "--rule", "999"],
        ["--pattern", "rule", "--seed", "128"],
        ["--pattern", "text", "--text", ""],
    ],
)
def test_invalid_config_preserves_previous_file(tmp_path, options) -> None:
    _run("init", str(tmp_path), "--name", "Test")
    config = tmp_path / ".git-tape/config.json"
    before = config.read_bytes()
    with pytest.raises(ValueError):
        _run("config", str(tmp_path), *options)
    assert config.read_bytes() == before


def test_init_and_config_preserve_git_identity(tmp_path) -> None:
    init_repo(tmp_path)
    for key, value in [
        ("user.name", "Original Developer"),
        ("user.email", "original@example.invalid"),
    ]:
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    _run("init", str(tmp_path), "--name", "Pixel Artist")
    _run("config", str(tmp_path), "--pattern", "rule", "--rule", "30")
    assert (
        subprocess.check_output(["git", "config", "user.name"], cwd=tmp_path, text=True).strip()
        == "Original Developer"
    )
    assert (
        subprocess.check_output(["git", "config", "user.email"], cwd=tmp_path, text=True).strip()
        == "original@example.invalid"
    )


def test_invalid_init_does_not_create_repository(tmp_path) -> None:
    target = tmp_path / "canvas"
    with pytest.raises(ValueError):
        _run("init", str(target), "--name", "Test", "--pattern", "rule", "--rule", "999")
    assert not target.exists()


@pytest.fixture
def isolated_git_identity(tmp_path, monkeypatch):
    settings = tmp_path / "global.gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(settings))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    return settings


def test_init_uses_global_git_name(tmp_path, isolated_git_identity) -> None:
    isolated_git_identity.write_text("[user]\nname = Global Developer\n")
    repo = tmp_path / "canvas"
    _run("init", str(repo))
    assert load_config(repo).author_name == "Global Developer"


def test_init_prefers_repository_git_name(tmp_path, isolated_git_identity) -> None:
    isolated_git_identity.write_text("[user]\nname = Global Developer\n")
    repo = tmp_path / "canvas"
    init_repo(repo)
    subprocess.run(["git", "config", "user.name", "Local Developer"], cwd=repo, check=True)
    _run("init", str(repo))
    assert load_config(repo).author_name == "Local Developer"


def test_missing_git_name_requires_explicit_name(tmp_path, isolated_git_identity) -> None:
    repo = tmp_path / "canvas"
    with pytest.raises(ValueError, match="--name"):
        _run("init", str(repo))
    assert not repo.exists()
    _run("init", str(repo), "--name", "Explicit Artist")
    _run("config", str(repo), "--pattern", "text")
    assert load_config(repo).author_name == "Explicit Artist"


@pytest.mark.parametrize("pattern", [
    {"type": "text", "text": "HELLO", "gap": 3, "message_gap": 9},
    {"type": "primes", "gap": 5},
])
def test_identity_update_preserves_pattern_spacing(tmp_path, pattern) -> None:
    from datetime import date

    from git_tape.config import TapeConfig, save_config

    config = TapeConfig(date(2026, 1, 4), (0, 1, 2, 3, 4), "Original", pattern)
    save_config(tmp_path, config)
    _run("config", str(tmp_path), "--name", "Changed", "--pattern", pattern["type"])
    assert load_config(tmp_path).pattern == config.pattern
    if pattern["type"] == "text":
        _run("config", str(tmp_path), "--text", "NEW")
        updated = load_config(tmp_path).pattern
        assert (updated.text, updated.gap, updated.message_gap) == ("NEW", 3, 9)


def test_email_precedence(tmp_path, isolated_git_identity, monkeypatch) -> None:
    from git_tape.cli import _author_email

    isolated_git_identity.write_text("[user]\nemail = global@example.invalid\n")
    monkeypatch.delenv("GIT_TAPE_EMAIL", raising=False)
    init_repo(tmp_path)
    assert _author_email(tmp_path) == "global@example.invalid"
    subprocess.run(
        ["git", "config", "user.email", "local@example.invalid"], cwd=tmp_path, check=True
    )
    assert _author_email(tmp_path) == "local@example.invalid"
    monkeypatch.setenv("GIT_TAPE_EMAIL", "env@example.invalid")
    assert _author_email(tmp_path) == "env@example.invalid"
    assert _author_email(tmp_path, "explicit@example.invalid") == "explicit@example.invalid"


@pytest.mark.parametrize("payload, message", [
    (None, "no canvas configuration"),
    ("{", "invalid canvas configuration"),
    ("[]", "JSON object"),
    ('{"version": 2}', "config needs epoch_sunday"),
])
def test_config_errors_are_actionable(tmp_path, monkeypatch, capsys, payload, message) -> None:
    from git_tape.cli import main

    if payload is not None:
        path = tmp_path / ".git-tape/config.json"
        path.parent.mkdir()
        path.write_text(payload)
    monkeypatch.setattr("sys.argv", ["git-tape", "preview", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert message in error
    assert "Traceback" not in error


def test_cli_reports_retained_recovery_files(tmp_path, monkeypatch, capsys) -> None:
    from git_tape import gitrepo
    from git_tape.cli import main

    _run("init", str(tmp_path), "--name", "Test", "--pattern", "rule", "--rule", "255",
         "--seed", "127", "--epoch", "2026-01-04", "--commits-per-pixel", "1")
    original = gitrepo._run

    def fail_after_commit(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if args[0] == "commit":
            raise gitrepo.GitError("simulated failure after commit")
        return result

    monkeypatch.setattr(gitrepo, "_run", fail_after_commit)
    monkeypatch.setattr("sys.argv", [
        "git-tape", "paint", str(tmp_path), "--email", "test@example.invalid",
        "--since", "2026-01-05", "--until", "2026-01-05", "--allow-future",
    ])
    with pytest.raises(SystemExit):
        main()
    error = capsys.readouterr().err
    assert "Retained private index" in error
    assert "inspect HEAD" in error
    assert str(tmp_path / ".git/index.lock") in error
    assert (tmp_path / ".git/index.lock").exists()
    assert list((tmp_path / ".git").glob("git-tape-index-*/commit-pending"))
