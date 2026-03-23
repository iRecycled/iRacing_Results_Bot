import logging
from logging.handlers import RotatingFileHandler
import os
from dotenv import load_dotenv

load_dotenv()

# Use INFO for debugging, WARNING for production
LOG_LEVEL = logging.INFO if os.getenv("DEBUG_MODE", "true").lower() == "true" else logging.WARNING

# Rate limit logger (separate from root logger)
_rate_limit_logger = None


def _get_rate_limit_logger():
    """Get or create the dedicated rate limit logger with its own rotating file."""
    global _rate_limit_logger
    if _rate_limit_logger is not None:
        return _rate_limit_logger

    _rate_limit_logger = logging.getLogger("rate_limit")
    _rate_limit_logger.setLevel(logging.INFO)
    _rate_limit_logger.propagate = False  # Don't duplicate to root/bot.log

    formatter = logging.Formatter(fmt="%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler = RotatingFileHandler(
        filename="rate_limits.log",
        maxBytes=512 * 1024,  # 512KB
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    _rate_limit_logger.addHandler(handler)

    return _rate_limit_logger


def append_rate_limit_log(line):
    """Append a line to the rate_limits.log via the dedicated rotating logger."""
    _get_rate_limit_logger().info(line)


def setup_logging():
    """
    Configure logging with file (all levels) and console (errors only) handlers.

    Configuration:
    - File: RotatingFileHandler with all logs
      - maxBytes: 1MB per log file
      - backupCount: Keep 2 backup files (total ~3MB max)
      - Log files: bot.log, bot.log.1, bot.log.2
      - Level: INFO (or WARNING if not in DEBUG_MODE)
    - Console: StreamHandler for errors only
      - Level: ERROR
    """
    # Get root logger
    logger = logging.getLogger()

    # Only configure if not already configured
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)

    # Set formatter for both handlers
    formatter = logging.Formatter(fmt="%(asctime)s - %(message)s", datefmt="%d-%b-%y %H:%M:%S")

    # Create rotating file handler (all messages)
    file_handler = RotatingFileHandler(
        filename="bot.log",
        maxBytes=1 * 1024 * 1024,  # 1MB
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Create console handler (errors only)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
