"""
Logging configuration for FinGuru.
Uses loguru for structured, colored logging.
"""

import sys
from loguru import logger
from app.core.config import settings


def setup_logging() -> None:
    """
    Configure loguru logger with custom format and level.
    """
    # Remove default handler
    logger.remove()

    # Add custom handler with format
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        level=settings.log_level,
        colorize=True,
    )

    # Add file handler for production
    logger.add(
        "logs/finguru_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    logger.info(f"Logging initialized at {settings.log_level} level")


def get_logger(name: str):
    """Get a logger instance for a specific module."""
    return logger.bind(name=name)
