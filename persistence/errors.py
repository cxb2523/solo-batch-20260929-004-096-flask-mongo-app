"""Persistence-layer exception hierarchy.

Routes decide the HTTP status per endpoint; business-level "nothing found"
is never an exception -- DAOs return ``None`` / empty results for that.
Only infrastructure problems raise, so they can be mapped to 503.
"""


class DaoError(Exception):
    """Base class for every persistence failure."""


class ConnectionUnavailable(DaoError):
    """Mongo server cannot be reached (connection refused / DNS / dropped)."""


class DaoTimeout(DaoError):
    """A database operation exceeded the configured timeout."""
