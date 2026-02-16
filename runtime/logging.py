"""Centralized logging configuration for the beancount project.

Usage:
    from beanbeaver.runtime import get_logger
    logger = get_logger(__name__)

    logger.debug("Detailed debug info")
    logger.info("General info")
    logger.warning("Warning message")
    logger.error("Error message")

Environment variables:
    BEANCOUNT_LOG_LEVEL: Set log level (DEBUG, INFO, WARNING, ERROR). Default: INFO
"""

import logging
import os
import sys

# Default log level, can be overridden by environment variable
DEFAULT_LOG_LEVEL = logging.INFO

# Format for log messages
LOG_FORMAT = "%(levelname)s [%(name)s] %(message)s"
LOG_FORMAT_DEBUG = "%(levelname)s [%(name)s:%(lineno)d] %(message)s"

# Track if logging has been configured
_logging_configured = False


def configure_logging(level: int | None = None) -> None:
    """Configure the root logger for the application.

    Args:
        level: Log level to use. If None, reads from BEANCOUNT_LOG_LEVEL env var
               or uses DEFAULT_LOG_LEVEL.
    """
    global _logging_configured

    if _logging_configured:
        return

    # Determine log level
    if level is None:
        env_level = os.environ.get("BEANCOUNT_LOG_LEVEL", "").upper()
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "WARN": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        level = level_map.get(env_level, DEFAULT_LOG_LEVEL)

    # Choose format based on level
    log_format = LOG_FORMAT_DEBUG if level == logging.DEBUG else LOG_FORMAT

    # Configure root logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(log_format))

    # Configure the beancount namespace
    root_logger = logging.getLogger("beancount_local")
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    root_logger.propagate = False

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name.

    Args:
        name: Module name, typically __name__

    Returns:
        Configured logger instance
    """
    # Ensure logging is configured
    configure_logging()

    # Create logger under our namespace
    logger_name = f"beancount_local.{name}"
    return logging.getLogger(logger_name)


def set_log_level(level: int) -> None:
    """Change the log level at runtime.

    Args:
        level: New log level (e.g., logging.DEBUG)
    """
    logger = logging.getLogger("beancount_local")
    logger.setLevel(level)

    # Update format if switching to/from DEBUG
    for handler in logger.handlers:
        if level == logging.DEBUG:
            handler.setFormatter(logging.Formatter(LOG_FORMAT_DEBUG))
        else:
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
