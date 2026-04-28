"""Structured workflow events for pid."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO


@dataclass(frozen=True)
class WorkflowEvent:
    """A structured workflow event emitted by core and extensions."""

    name: str
    step: str = ""
    level: str = "info"
    message: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable event dictionary."""

        data: dict[str, Any] = {
            "timestamp": self.timestamp,
            "name": self.name,
            "level": self.level,
        }
        if self.step:
            data["step"] = self.step
        if self.message:
            data["message"] = self.message
        if self.fields:
            data["fields"] = self.fields
        return data

    def to_json(self) -> str:
        """Serialize the event as one compact JSON object."""

        return json.dumps(self.to_dict(), sort_keys=True, default=str)


class EventSink(Protocol):
    """Receiver for structured workflow events."""

    def emit(self, event: WorkflowEvent) -> None:
        """Handle a workflow event."""


class NullEventSink:
    """Event sink that intentionally drops every event."""

    def emit(self, event: WorkflowEvent) -> None:
        _ = event


class JsonlEventSink:
    """Append workflow events to a JSONL file or text stream."""

    def __init__(self, target: Path | str | TextIO) -> None:
        self._stream: TextIO | None = None
        self._path: Path | None = None
        if isinstance(target, Path | str):
            self._path = Path(target)
        else:
            self._stream = target

    def emit(self, event: WorkflowEvent) -> None:
        line = event.to_json() + "\n"
        if self._stream is not None:
            self._stream.write(line)
            self._stream.flush()
            return
        if self._path is None:  # pragma: no cover - constructor prevents this
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(line)


class CompositeEventSink:
    """Fan out events to multiple sinks."""

    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    def emit(self, event: WorkflowEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)


class ListEventSink:
    """In-memory event sink useful for tests and embedding."""

    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)
