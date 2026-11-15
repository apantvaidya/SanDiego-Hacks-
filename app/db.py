"""Database connection helper. Owned by the Neon setup issue."""
from contextlib import contextmanager


@contextmanager
def get_conn():
    """Yield a psycopg connection to settings.database_url (autocommit off, dict rows)."""
    raise NotImplementedError
    yield
