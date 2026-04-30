"""Centralized logger configuration. Every script writes to logs/<name>.log."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import LOG_DIR

_FMT = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(level)
    log.propagate = False

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    log.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(_FMT))
    log.addHandler(sh)
    return log


def jsonl_path(name: str) -> Path:
    return LOG_DIR / f"{name}.jsonl"
