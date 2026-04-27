"""Argument parsing helpers."""

from __future__ import annotations

import re

from orcha.errors import abort
from orcha.models import ParsedArgs
from orcha.output import echo_err, echo_out

USAGE = "usage: orcha [ATTEMPTS] [THINKING] BRANCH PROMPT..."
THINKING_LEVELS = ("low", "medium", "high", "xhigh")


def parse_args(argv: list[str]) -> ParsedArgs:
    """Parse Orcha's fish-compatible positional argument format."""

    if not argv or argv[0] in {"--help", "-h"}:
        echo_out(USAGE)
        abort(0)

    args = list(argv)
    max_attempts = 3
    if re.fullmatch(r"[0-9]+", args[0]):
        attempts = args.pop(0)
        if re.fullmatch(r"[1-9][0-9]*", attempts) is None:
            echo_err("orcha: ATTEMPTS must be a positive integer")
            echo_err(USAGE)
            abort(2)
        max_attempts = int(attempts)

    thinking_level = "medium"
    if args and args[0] in THINKING_LEVELS:
        thinking_level = args.pop(0)

    if not args:
        echo_err("orcha: branch required")
        echo_err(USAGE)
        abort(2)

    branch = args.pop(0)
    prompt = " ".join(args)
    if not branch:
        echo_err("orcha: branch must be non-empty")
        echo_err(USAGE)
        abort(2)
    if not prompt:
        echo_err("orcha: prompt required for non-interactive pi -p flow")
        echo_err(USAGE)
        abort(2)

    return ParsedArgs(max_attempts, thinking_level, branch, prompt)


def bump_thinking(level: str) -> str:
    """Return the next supported pi thinking level, or the input when at max."""

    try:
        index = THINKING_LEVELS.index(level)
    except ValueError:
        return level
    if index >= len(THINKING_LEVELS) - 1:
        return level
    return THINKING_LEVELS[index + 1]
