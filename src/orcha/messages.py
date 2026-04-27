"""Commit and pull-request message parsing and validation."""

from __future__ import annotations

import json
from typing import Any

from orcha.errors import abort
from orcha.models import CommitMessage
from orcha.output import echo_err

MESSAGE_BODY_LIMIT = 20_000
MESSAGE_TITLE_LIMIT = 200


def parse_commit_message(raw: str) -> CommitMessage:
    """Parse and sanitize generated commit/PR metadata JSON."""

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as error:
        echo_err(f"orcha: agent message output is not valid JSON: {error}")
        abort(1)

    if not isinstance(data, dict):
        echo_err("orcha: agent message output must be a JSON object")
        abort(1)

    title = _string_field(data, "title").strip()
    body = _string_field(data, "body").strip()

    if not title:
        echo_err("orcha: agent message title is empty")
        abort(1)
    if "\n" in title or "\r" in title:
        echo_err("orcha: agent message title must be one line")
        abort(1)
    if len(title) > MESSAGE_TITLE_LIMIT:
        echo_err(f"orcha: agent message title exceeds {MESSAGE_TITLE_LIMIT} characters")
        abort(1)
    if not body:
        echo_err("orcha: agent message body is empty")
        abort(1)
    if len(body) > MESSAGE_BODY_LIMIT:
        echo_err(f"orcha: agent message body exceeds {MESSAGE_BODY_LIMIT} characters")
        abort(1)
    if "\0" in title or "\0" in body:
        echo_err("orcha: agent message contains a NUL byte")
        abort(1)

    return CommitMessage(title=title, body=body)


def _string_field(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if isinstance(value, str):
        return value
    echo_err(f"orcha: agent message field {field!r} must be a string")
    abort(1)
