"""Database access. All sessions run in UTC so hour/day buckets are unambiguous."""
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
_CONN_KWARGS = {"row_factory": dict_row, "autocommit": True, "options": "-c timezone=UTC"}
_pool = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.pool_max,
            timeout=10,
            kwargs=dict(_CONN_KWARGS),
            open=True,
        )
    return _pool


@contextmanager
def connection():
    """Borrow a pooled connection (autocommit; use conn.transaction() for atomic blocks)."""
    with get_pool().connection() as conn:
        yield conn


def connect() -> psycopg.Connection:
    """Standalone connection for scripts."""
    return psycopg.connect(settings.database_url, **_CONN_KWARGS)


def apply_sql_file(conn, name: str) -> None:
    conn.execute((SQL_DIR / name).read_text())
