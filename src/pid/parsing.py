"""Argument parsing helpers."""

from __future__ import annotations

import re

from pid.config import DEFAULT_THINKING_LEVELS
from pid.errors import abort
from pid.models import ParsedArgs
from pid.output import echo_err, echo_out

USAGE = "usage: pid run [ATTEMPTS] [THINKING] BRANCH PROMPT..."
SESSION_USAGE = "usage: pid session [ATTEMPTS] [THINKING] BRANCH [PROMPT...]"
THINKING_LEVELS = DEFAULT_THINKING_LEVELS


def parse_args(
    argv: list[str],
    *,
    default_thinking: str = "medium",
    thinking_levels: tuple[str, ...] = THINKING_LEVELS,
) -> ParsedArgs:
    """Parse pid's positional argument format."""

    if not argv or argv[0] in {"--help", "-h"}:
        echo_out(USAGE)
        abort(0)

    args = list(argv)
    interactive = False
    usage = USAGE
    if args[0] == "session":
        interactive = True
        usage = SESSION_USAGE
        args.pop(0)
        if not args or args[0] in {"--help", "-h"}:
            echo_out(SESSION_USAGE)
            abort(0)

    max_attempts = 3
    if re.fullmatch(r"[0-9]+", args[0]):
        attempts = args.pop(0)
        if re.fullmatch(r"[1-9][0-9]*", attempts) is None:
            echo_err("pid: ATTEMPTS must be a positive integer")
            echo_err(usage)
            abort(2)
        max_attempts = int(attempts)

    thinking_level = default_thinking
    if args and args[0] in thinking_levels:
        thinking_level = args.pop(0)

    if not args:
        echo_err("pid: branch required")
        echo_err(usage)
        abort(2)

    branch = args.pop(0)
    prompt = " ".join(args)
    if not branch:
        echo_err("pid: branch must be non-empty")
        echo_err(usage)
        abort(2)
    interactive_prompt = prompt if interactive and prompt else None
    if not prompt and not interactive:
        echo_err("pid: prompt required for non-interactive agent flow")
        echo_err(usage)
        abort(2)
    if not prompt:
        prompt = "Interactive agent session."

    return ParsedArgs(
        max_attempts,
        thinking_level,
        branch,
        prompt,
        interactive,
        interactive_prompt,
    )


def bump_thinking(
    level: str, thinking_levels: tuple[str, ...] = THINKING_LEVELS
) -> str:
    """Return the next supported thinking level, or input when at max."""

    try:
        index = thinking_levels.index(level)
    except ValueError:
        return level
    if index >= len(thinking_levels) - 1:
        return level
    return thinking_levels[index + 1]
