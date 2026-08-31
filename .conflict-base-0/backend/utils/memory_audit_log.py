"""Operational audit logging for durable memory writes.

Root logging defaults to WARNING in production, so memory save INFO lines
never reached backend_startup.log. This logger always emits at INFO to
stdout (captured by nohup) and to logs/memory_audit.log.
"""
from __future__ import annotations

import logging
import os
import sys

_LOGGER: logging.Logger | None = None


def _memory_audit_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger("guaardvark.memory_audit")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [PID:%(process)d TID:%(thread)d] : %(message)s"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_dir = os.environ.get("GUAARDVARK_LOG_DIR", "logs")
    if not os.path.isabs(log_dir):
        root = os.environ.get("GUAARDVARK_ROOT", ".")
        log_dir = os.path.join(root, log_dir)
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(
        os.path.join(log_dir, "memory_audit.log"),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    _LOGGER = logger
    return logger


def log_memory_saved(memory_id: str, memory_type: str, source: str, content: str) -> None:
    preview = " ".join((content or "").split())[:160]
    _memory_audit_logger().info(
        "Memory saved: id=%s type=%s source=%s preview=%r",
        memory_id,
        memory_type,
        source,
        preview,
    )


def log_memory_rejected(reason: str, source: str, content: str = "") -> None:
    preview = " ".join((content or "").split())[:160]
    _memory_audit_logger().warning(
        "Memory rejected: source=%s reason=%s preview=%r",
        source,
        reason,
        preview,
    )


def log_memory_failed(source: str, error: str, content: str = "") -> None:
    preview = " ".join((content or "").split())[:160]
    _memory_audit_logger().error(
        "Memory save failed: source=%s error=%s preview=%r",
        source,
        error,
        preview,
    )
