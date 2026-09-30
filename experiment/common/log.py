"""Short, human-oriented messages for the command line.

Python's `warnings` module prints the file, line and source of the code that
raised a warning, which is noise for a CLI user. Use `info`, `warn` and
`error` for messages meant for people, and call `install_warning_format` at
entry points so third-party warnings are shortened the same way.
"""

import sys
import warnings

_COLORS = {"info": "", "warn": "\033[33m", "error": "\033[31m"}
_RESET = "\033[0m"


def _emit(level: str, message: object) -> None:
    stream = sys.stderr
    prefix = f"{level}:"
    color = _COLORS[level]
    if color and stream.isatty():
        prefix = f"{color}{prefix}{_RESET}"
    print(f"{prefix} {message}", file=stream, flush=True)


def info(message: object) -> None:
    _emit("info", message)


def warn(message: object) -> None:
    _emit("warn", message)


def error(message: object) -> None:
    _emit("error", message)


def _format_warning(message, category, filename, lineno, line=None) -> str:
    return f"warning: {message}\n"


def install_warning_format() -> None:
    warnings.formatwarning = _format_warning
