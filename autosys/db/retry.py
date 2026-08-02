"""
Database retry utility.

Provides a decorator/context manager that retries SQLAlchemy operations
on ``OperationalError`` with exponential backoff.

Usage
-----
    from autosys.db.retry import retry_db

    @retry_db(max_attempts=3, base_delay=1.0)
    def my_db_operation(session):
        session.execute(text("SELECT 1"))

Or as a context manager:

    with retry_db(max_attempts=3):
        with sync_session() as session:
            session.execute(text("SELECT 1"))
"""
from __future__ import annotations

import functools
import time
from typing import Callable, TypeVar

from loguru import logger
from sqlalchemy.exc import OperationalError

T = TypeVar("T")


def retry_db(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
) -> Callable:
    """
    Decorator that retries a function on ``OperationalError``.

    Parameters
    ----------
    max_attempts:
        Maximum number of attempts (including the first).
    base_delay:
        Initial delay in seconds before the first retry.
    backoff_factor:
        Multiplier applied to the delay after each failed attempt.
        Default: 2.0 → delays of base_delay, base_delay*2, base_delay*4, ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except OperationalError as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        logger.warning(
                            "DB operation failed (attempt %d/%d): %s — retrying in %.1fs",
                            attempt, max_attempts, exc, delay,
                        )
                        time.sleep(delay)
                        delay *= backoff_factor
                    else:
                        logger.error(
                            "DB operation failed after %d attempts: %s",
                            max_attempts, exc,
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


class retry_db_ctx:
    """
    Context manager version of :func:`retry_db`.

    Usage::

        with retry_db_ctx(max_attempts=3):
            with sync_session() as session:
                session.execute(text("SELECT 1"))
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor

    def __enter__(self) -> "retry_db_ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False  # don't suppress — use the decorator for retry logic
