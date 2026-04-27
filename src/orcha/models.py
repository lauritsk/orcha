"""Shared data models for Orcha."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    """Captured command result."""

    returncode: int
    stdout: str
    stderr: str = ""


@dataclass(frozen=True)
class ParsedArgs:
    """Parsed Orcha positional arguments."""

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
