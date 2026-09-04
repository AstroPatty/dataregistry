from typing import Annotated, Iterator

from fastapi import Depends
from sqlalchemy import Connection, Engine, create_engine

from dataregistry_api.config import (
    DatabasePoolSettings,
    get_database_connection_url,
)

_ENGINE: Engine | None = None

#: Whether this module created ``_ENGINE`` and is therefore responsible for
#: disposing of it. An engine installed by :func:`set_engine` is owned by the
#: caller (e.g. a test fixture) and is left alone on shutdown.
_ENGINE_OWNED: bool = False


def create_database_engine() -> Engine:
    """Build a new engine from the environment configuration."""
    pool = DatabasePoolSettings()
    return create_engine(
        get_database_connection_url(),
        pool_size=pool.pool_size,
        max_overflow=pool.max_overflow,
        pool_timeout=pool.pool_timeout,
        pool_recycle=pool.pool_recycle,
        pool_pre_ping=True,
    )


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use.

    The engine is built lazily rather than at import time so that importing
    the application does not require a valid database configuration, and so
    tests can install their own engine via :func:`set_engine`.
    """
    global _ENGINE, _ENGINE_OWNED
    if _ENGINE is None:
        _ENGINE = create_database_engine()
        _ENGINE_OWNED = True
    return _ENGINE


def set_engine(engine: Engine | None) -> Engine | None:
    """Install a caller-owned engine, returning the one it replaced.

    The caller keeps responsibility for disposing of ``engine``;
    :func:`dispose_engine` will not touch it.
    """
    global _ENGINE, _ENGINE_OWNED
    previous = _ENGINE
    _ENGINE = engine
    _ENGINE_OWNED = False
    return previous


def dispose_engine() -> None:
    """Dispose of the engine, if this module owns it."""
    global _ENGINE, _ENGINE_OWNED
    if _ENGINE is not None and _ENGINE_OWNED:
        _ENGINE.dispose()
        _ENGINE = None
        _ENGINE_OWNED = False


def get_database_connection() -> Iterator[Connection]:
    with get_engine().begin() as conn:
        yield conn


ConnectionDependency = Annotated[Connection, Depends(get_database_connection)]
