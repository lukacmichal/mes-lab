"""MES lab: a virtual automotive electronics plant and its MES."""

from pathlib import Path

__version__ = "0.8.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


def setup_logging(level: int = 20) -> None:
    """INFO for our own messages, WARNING for chatty libraries."""
    import logging

    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore", "pgserver"):
        logging.getLogger(name).setLevel(logging.WARNING)
