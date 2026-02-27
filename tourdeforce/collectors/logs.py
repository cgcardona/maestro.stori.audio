"""LogCollector — structured JSON logging to files and console."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, default=str)


class LogCollector:
    """Sets up structured logging for the Tour de Force harness."""

    def __init__(self, output_dir: Path, *, verbose: bool = False) -> None:
        self._log_dir = output_dir / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger("tourdeforce")
        root.setLevel(logging.DEBUG if verbose else logging.INFO)
        root.handlers.clear()

        # File handler — JSON lines
        fh = logging.FileHandler(self._log_dir / "client.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JSONFormatter())
        root.addHandler(fh)

        # Console handler — human-readable
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(ch)

        self._logger = root

    @property
    def log_dir(self) -> Path:
        return self._log_dir
