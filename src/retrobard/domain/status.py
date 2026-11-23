# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""What RetroBard is doing, reported as events instead of printed text.

The command line prints them; a graphical interface can show them as a status
line, a log, or a notification. Components never print directly.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StatusKind(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    DEBUG = "debug"  # only interesting when troubleshooting
    # A line is being voiced. data: speaker, voice_id, text, chars, cached
    LINE = "line"
    # A line was skipped or cut off. data: reason
    LINE_SKIPPED = "line_skipped"
    # The game window appeared or went away. data: available
    WINDOW = "window"
    # Letter shapes were seen that the taught font does not know. data: count
    UNKNOWN_LETTERS = "unknown_letters"
    # The session started or stopped. data: running
    SESSION = "session"


@dataclass(frozen=True)
class StatusEvent:
    kind: StatusKind
    message: str
    data: dict[str, Any] = field(default_factory=dict)


StatusSink = Callable[[StatusEvent], None]


def report(sink: StatusSink, kind: StatusKind, message: str, **data: Any) -> None:
    sink(StatusEvent(kind=kind, message=message, data=data))


def null_status(_event: StatusEvent) -> None:
    """Discards events."""


def make_console_status(verbose: bool = False) -> StatusSink:
    """Prints events the way the command line always has; DEBUG only when verbose."""

    def sink(event: StatusEvent) -> None:
        if event.kind == StatusKind.DEBUG and not verbose:
            return
        print(event.message, flush=True)

    return sink


console_status: StatusSink = make_console_status(verbose=False)
