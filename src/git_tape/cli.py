from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from .calendar import sunday_on_or_before
from .config import (
    TapeConfig,
    levels_from_max_commits,
    load_config,
    normalize_commit_levels,
    save_config,
)
from .gallery import render_rule_gallery
from .gitrepo import (
    GitError,
    configured_author_email,
    configured_author_name,
    init_repo,
    set_local_config,
)
from .patterns import (
    PatternConfig,
    PrimeConfig,
    RuleConfig,
    TextConfig,
    pattern_config_dict,
    pattern_from_config,
)
from .planner import paint
from .render import render_window


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _commit_levels_csv(value: str) -> list[int]:
    try:
        levels = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    try:
        return normalize_commit_levels(levels)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _pattern_dict_from_values(pattern: str, text: str, rule: int, seed: int) -> PatternConfig:
    if pattern == "text":
        return TextConfig(text)
    if pattern == "primes":
        return PrimeConfig()
    if pattern == "rule":
        return RuleConfig(rule, seed)
    raise AssertionError(pattern)


def _pattern_dict(args: argparse.Namespace, fallback: PatternConfig | None = None) -> PatternConfig:
    if fallback is not None and (args.pattern is None or args.pattern == fallback.type):
        if isinstance(fallback, TextConfig):
            return replace(fallback, text=args.text if args.text is not None else fallback.text)
        if isinstance(fallback, RuleConfig):
            return replace(
                fallback,
                rule=args.rule if args.rule is not None else fallback.rule,
                seed=args.seed if args.seed is not None else fallback.seed,
            )
        return fallback
    previous = pattern_config_dict(fallback) if fallback is not None else {}
    pattern = args.pattern or previous.get("type", "primes")
    text = args.text if args.text is not None else previous.get("text", "METRICS ARE NOT WORK")
    rule = args.rule if args.rule is not None else int(previous.get("rule", 30))
    seed = args.seed if args.seed is not None else int(previous.get("seed", 0b0001000))
    return _pattern_dict_from_values(pattern, text, rule, seed)


def _commit_levels(
    args: argparse.Namespace, fallback: TapeConfig | None = None
) -> tuple[int, int, int, int, int]:
    if getattr(args, "level_commits", None) is not None:
        return tuple(args.level_commits)
    if getattr(args, "commits_per_pixel", None) is not None:
        return tuple(levels_from_max_commits(args.commits_per_pixel))
    if fallback is not None:
        return fallback.commit_levels
    return tuple(levels_from_max_commits(3))


def _build_config(args: argparse.Namespace, fallback: TapeConfig | None = None) -> TapeConfig:
    epoch = args.epoch
    if epoch is None:
        if fallback is not None:
            epoch = fallback.epoch_sunday
        else:
            epoch = sunday_on_or_before(date.today() - timedelta(days=364))
    author_name = args.name
    if author_name is None:
        if fallback is not None:
            author_name = fallback.author_name
        else:
            author_name = configured_author_name(Path(args.repo).resolve())
    if not author_name or not author_name.strip():
        raise ValueError(
            "Provide --name or configure Git with `git config --global user.name ...`."
        )
    pattern = _pattern_dict(args, fallback.pattern if fallback else None)
    commit_levels = _commit_levels(args, fallback)
    return TapeConfig(
        epoch_sunday=epoch,
        commit_levels=commit_levels,
        author_name=author_name,
        pattern=pattern,
    )


def _maybe_set_email(repo: Path, args: argparse.Namespace) -> None:
    if getattr(args, "email", None):
        set_local_config(repo, "user.email", args.email)


def _author_email(repo: Path, explicit: str | None = None) -> str:
    email = explicit or os.environ.get("GIT_TAPE_EMAIL") or configured_author_email(repo)
    if not email:
        raise SystemExit(
            "Provide --email, set GIT_TAPE_EMAIL, or configure this repo with "
            "`git config user.email ...`."
        )
    return email


def cmd_init(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config = _build_config(args)
    init_repo(repo)
    save_config(repo, config)
    _maybe_set_email(repo, args)
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# git-tape canvas\n\n"
            "This repository intentionally turns the GitHub contribution calendar into pixel art.\n"
            "The commits are generated by `git-tape`; the graph is art, not a productivity claim.\n",
            encoding="utf-8",
        )
    print(f"Initialized canvas at {repo}")
    print(f"Epoch: {config.epoch_sunday} (Sunday)")
    print(f"Commit levels: {list(config.commit_levels)}")
    print("Nothing has been committed yet; the first painted pixel will include the canvas files.")


def cmd_config(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    current = load_config(repo)
    config = _build_config(args, fallback=current)
    save_config(repo, config)
    _maybe_set_email(repo, args)
    print(f"Updated config for {repo}")
    print(f"Epoch: {config.epoch_sunday} (Sunday)")
    print(f"Commit levels: {list(config.commit_levels)}")
    print(f"Pattern: {pattern_config_dict(config.pattern)}")


def cmd_preview(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config = load_config(repo)
    pattern = pattern_from_config(config.pattern)
    end = args.until or date.today()
    print(render_window(pattern, config.epoch_sunday, end=end, weeks=args.weeks))


def cmd_paint(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config = load_config(repo)
    email = _author_email(repo, args.email)

    end = args.until or date.today()
    start = args.since or (end - timedelta(days=364))
    if end > date.today() and not args.allow_future:
        raise SystemExit(
            "Refusing to create future-dated commits; use --allow-future if you really mean it."
        )

    result = paint(
        repo=repo,
        config=config,
        author_email=email,
        start=start,
        end=end,
        allow_backfill=args.allow_backfill,
    )
    print(f"Created {result.created} commits across {result.lit_days} lit days.")
    if result.skipped_existing:
        print(f"Kept {result.skipped_existing} already-painted commits (idempotent rerun).")


def cmd_rule_gallery(args: argparse.Namespace) -> None:
    print(
        render_rule_gallery(
            start_rule=args.start,
            end_rule=args.end,
            weeks=args.weeks,
            columns=args.columns,
            seed=args.seed,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-tape",
        description="A deterministic 7-pixel-high tape rendered into a GitHub contribution calendar.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="initialize a dedicated art repository")
    p_init.add_argument("repo")
    p_init.add_argument("--name", help="Git author name (default: configured Git user.name)")
    p_init.add_argument("--email", help="optional repo-local Git email for painted commits")
    p_init.add_argument("--epoch", type=_iso_date, help="Sunday that defines tape column zero")
    p_init.add_argument("--commits-per-pixel", type=int, default=3, help="brightest commit count")
    p_init.add_argument(
        "--level-commits",
        type=_commit_levels_csv,
        help="five comma-separated commit counts, e.g. 0,25,50,75,100",
    )
    p_init.add_argument("--pattern", choices=("text", "primes", "rule"), default="primes")
    p_init.add_argument("--text", default="METRICS ARE NOT WORK")
    p_init.add_argument("--rule", type=int, default=30)
    p_init.add_argument("--seed", type=lambda value: int(value, 0), default=0b0001000)
    p_init.set_defaults(func=cmd_init)

    p_config = sub.add_parser("config", help="update an existing canvas configuration")
    p_config.add_argument("repo")
    p_config.add_argument("--name")
    p_config.add_argument("--email", help="optional repo-local Git email for painted commits")
    p_config.add_argument("--epoch", type=_iso_date, help="Sunday that defines tape column zero")
    p_config.add_argument("--commits-per-pixel", type=int, help="brightest commit count")
    p_config.add_argument(
        "--level-commits",
        type=_commit_levels_csv,
        help="five comma-separated commit counts, e.g. 0,25,50,75,100",
    )
    p_config.add_argument("--pattern", choices=("text", "primes", "rule"))
    p_config.add_argument("--text")
    p_config.add_argument("--rule", type=int)
    p_config.add_argument("--seed", type=lambda value: int(value, 0))
    p_config.set_defaults(func=cmd_config)

    p_preview = sub.add_parser("preview", help="preview the moving contribution-calendar window")
    p_preview.add_argument("repo")
    p_preview.add_argument("--until", type=_iso_date)
    p_preview.add_argument("--weeks", type=int, default=53)
    p_preview.set_defaults(func=cmd_preview)

    p_paint = sub.add_parser("paint", help="create any missing pixel commits")
    p_paint.add_argument("repo")
    p_paint.add_argument("--email", help="GitHub-linked commit email; alternatively GIT_TAPE_EMAIL")
    p_paint.add_argument("--since", type=_iso_date)
    p_paint.add_argument("--until", type=_iso_date)
    p_paint.add_argument("--allow-backfill", action="store_true")
    p_paint.add_argument("--allow-future", action="store_true")
    p_paint.set_defaults(func=cmd_paint)

    p_gallery = sub.add_parser("rule-gallery", help="preview elementary cellular automaton rules")
    p_gallery.add_argument("--start", type=int, default=0, help="first rule to show (default: 0)")
    p_gallery.add_argument("--end", type=int, default=255, help="last rule to show (default: 255)")
    p_gallery.add_argument(
        "--weeks", type=int, default=16, help="weeks shown per rule (default: 16)"
    )
    p_gallery.add_argument(
        "--columns", type=int, default=4, help="rule previews per terminal row (default: 4)"
    )
    p_gallery.add_argument("--seed", type=lambda value: int(value, 0), default=0b0001000)
    p_gallery.set_defaults(func=cmd_rule_gallery)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (GitError, ValueError, OSError) as exc:
        parser.error("\n".join([str(exc), *getattr(exc, "__notes__", [])]))


if __name__ == "__main__":
    main()
