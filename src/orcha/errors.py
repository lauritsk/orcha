"""Internal control-flow errors."""

from __future__ import annotations

from typing import NoReturn


class OrchaAbort(Exception):
    """Internal control-flow exception carrying intended exit code."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(code)


def abort(code: int) -> NoReturn:
    """Stop the current Orcha flow with an exit code."""

    raise OrchaAbort(code)
