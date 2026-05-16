from __future__ import annotations

from pathlib import Path
import logging
import sys


LOGGER_NAME = "iac_tool"


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def configure_logging(verbose: bool = False, log_file: Path | None = None) -> None:
    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"),
    )
    logger.addHandler(console_handler)

    if log_file is not None:
        target = log_file.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(target, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"),
        )
        logger.addHandler(file_handler)


def format_exception_chain(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip() or current.__class__.__name__
        if not parts or text not in parts[-1]:
            parts.append(text)
        current = current.__cause__

    if not parts:
        return exc.__class__.__name__
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} | caused by: " + " | caused by: ".join(parts[1:])
