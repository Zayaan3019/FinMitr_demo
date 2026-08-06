"""
Logging configuration.

Two things here are not cosmetic.

**The stream is forced to UTF-8.** On Windows, ``sys.stdout`` defaults to the
system ANSI code page (cp1252 on an en-IN install). Any log line containing a
character outside that page -- a tick mark, an arrow, a rupee sign, or a
merchant name in Devanagari -- raises ``UnicodeEncodeError`` inside the loguru
sink. The application keeps running, but the line is replaced by a traceback
about the failure to log it, which is precisely backwards during an incident.
Bank narrations are attacker-influenced text, so this is reachable from data,
not just from source.

**Log lines never carry unredacted transaction data.** The redaction layer
protects the LLM egress path; a log file is a second egress path with a longer
retention period. Callers pass ids and counts.
"""

import io
import sys
from pathlib import Path

from loguru import logger

from app.core.config import settings


def _utf8_stream(stream):
    """
    Return ``stream`` reconfigured for UTF-8, replacing what it cannot encode.

    ``errors="replace"`` rather than ``"strict"``: losing a glyph is an
    acceptable degradation, losing the log line is not.
    """
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
            return stream
    except (AttributeError, ValueError):  # pragma: no cover - exotic streams
        pass
    try:
        return io.TextIOWrapper(
            stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except (AttributeError, ValueError):  # pragma: no cover
        return stream


def setup_logging() -> None:
    """Configure loguru. Idempotent -- safe to call from tests and from main."""
    logger.remove()

    logger.add(
        _utf8_stream(sys.stdout),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        level=settings.log_level,
        colorize=True,
        backtrace=False,
        # No local variables in tracebacks: a frame in the ingest path holds
        # narrations and amounts, and diagnose=True would write them to disk.
        diagnose=False,
    )

    Path("logs").mkdir(exist_ok=True)
    logger.add(
        "logs/finguru_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
        backtrace=False,
        diagnose=False,
    )

    logger.info(f"Logging initialized at {settings.log_level} level")


def get_logger(name: str):
    """Get a logger bound to a module name."""
    return logger.bind(name=name)
