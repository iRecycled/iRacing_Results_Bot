import logging
from logging.handlers import RotatingFileHandler
import os
from dotenv import load_dotenv

load_dotenv()

# Use INFO for debugging, WARNING for production
LOG_LEVEL = logging.INFO if os.getenv('DEBUG_MODE', 'true').lower() == 'true' else logging.WARNING

def setup_logging():
    """
    Configure logging with RotatingFileHandler to prevent log files from growing too large.

    Configuration:
    - maxBytes: 1MB per log file
    - backupCount: Keep 3 backup files (total ~3-4MB max)
    - Log files: bot.log, bot.log.1, bot.log.2, bot.log.3
    """
    # Get root logger
    logger = logging.getLogger()

    # Only configure if not already configured
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)

    # Create rotating file handler
    # maxBytes=1*1024*1024 = 1MB per file
    # backupCount=2 means keep 2 old files
    handler = RotatingFileHandler(
        filename='bot.log',
        maxBytes=1*1024*1024,  # 1MB
        backupCount=2,
        encoding='utf-8'
    )

    # Set formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(message)s',
        datefmt='%d-%b-%y %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(handler)

    return logger
