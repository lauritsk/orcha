from __future__ import annotations

import json

import pytest

from pid.errors import PIDAbort
from pid.messages import MESSAGE_BODY_LIMIT, MESSAGE_TITLE_LIMIT, parse_commit_message
from pid.models import CommitMessage


def test_parse_commit_message_strips_valid_title_and_body() -> None:
    assert parse_commit_message(
        json.dumps({"title": "  feat: useful thing  ", "body": "\n- Done.\n"})
    ) == CommitMessage("feat: useful thing", "- Done.")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "must be a JSON object"),
        (json.dumps({"title": "   ", "body": "body"}), "title is empty"),
        (
            json.dumps({"title": "feat: one\nline", "body": "body"}),
            "title must be one line",
        ),
        (
            json.dumps({"title": "x" * (MESSAGE_TITLE_LIMIT + 1), "body": "body"}),
            f"title exceeds {MESSAGE_TITLE_LIMIT} characters",
        ),
        (json.dumps({"title": "feat: ok", "body": "   "}), "body is empty"),
        (
            json.dumps({"title": "feat: ok", "body": "x" * (MESSAGE_BODY_LIMIT + 1)}),
            f"body exceeds {MESSAGE_BODY_LIMIT} characters",
        ),
        (json.dumps({"title": "feat: \u0000", "body": "body"}), "contains a NUL byte"),
        (json.dumps({"title": 123, "body": "body"}), "field 'title' must be a string"),
        (
            json.dumps({"title": "feat: ok", "body": None}),
            "field 'body' must be a string",
        ),
    ],
)
def test_parse_commit_message_rejects_malformed_metadata(
    raw: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        parse_commit_message(raw)

    assert exc_info.value.code == 1
    assert message in capsys.readouterr().err
