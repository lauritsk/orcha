"""Shared data models for pid."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OutputMode(StrEnum):
    """Console output detail level."""

    NORMAL = "normal"
    AGENT = "agent"
    ALL = "all"


@dataclass(frozen=True)
class CommandResult:
    """Captured command result."""

    returncode: int
    stdout: str
    stderr: str = ""


@dataclass(frozen=True)
class ParsedArgs:
    """Parsed pid positional arguments."""

    max_attempts: int
    thinking_level: str
    branch: str
    prompt: str
    interactive: bool = False
    interactive_prompt: str | None = None


@dataclass(frozen=True)
class CommitMessage:
    """Commit and pull-request message generated from completed work."""

    title: str
    body: str
