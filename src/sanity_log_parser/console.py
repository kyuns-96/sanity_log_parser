from __future__ import annotations

import os
import sys
from typing import TextIO


class Ansi:
    RESET: str = "\x1b[0m"
    BOLD: str = "\x1b[1m"
    DIM: str = "\x1b[2m"
    CYAN: str = "\x1b[36m"
    GREEN: str = "\x1b[32m"
    YELLOW: str = "\x1b[33m"
    RED: str = "\x1b[31m"


def supports_color(override: bool | None = None, stream: TextIO | None = None) -> bool:
    if "NO_COLOR" in os.environ:
        return False
    if override is False:
        return False
    if override is True:
        return True
    target = stream or sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


class Console:
    _KEY_WIDTH: int = 24

    def __init__(
        self, use_color: bool | None = None, stream: TextIO | None = None
    ) -> None:
        self.stream: TextIO = stream if stream is not None else sys.stdout
        self.use_color: bool = supports_color(use_color, self.stream)

    def _paint(self, text: str, code: str | None = None) -> str:
        if not self.use_color or not code:
            return text
        return f"{code}{text}{Ansi.RESET}"

    def section(self, title: str) -> None:
        print(self._paint(title, Ansi.BOLD), file=self.stream)

    def kv(self, key: str, value: object) -> None:
        label = f"{key}:".ljust(self._KEY_WIDTH)
        if self.use_color:
            label = self._paint(label, Ansi.CYAN)
        print(f"{label} {value}", file=self.stream)

    def _status(self, level: str, message: str, color: str) -> None:
        prefix = self._paint(f"[{level}]", color)
        print(f"{prefix} {message}", file=self.stream)

    def info(self, message: str) -> None:
        self._status("INFO", message, Ansi.CYAN)

    def warn(self, message: str) -> None:
        self._status("WARN", message, Ansi.YELLOW)

    def error(self, message: str) -> None:
        self._status("ERROR", message, Ansi.RED)

    def success(self, message: str) -> None:
        self._status("OK", message, Ansi.GREEN)


__all__ = [
    "Ansi",
    "Console",
    "supports_color",
]
