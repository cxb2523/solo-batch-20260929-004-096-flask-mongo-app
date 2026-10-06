"""Persistence layer: connection factory, entity DAOs and a swap-aware registry.

Design decisions (see task spec):

* Connection reuse -- a *process-wide lazy singleton* MongoClient guarded by a
  lock (no reconnect storm under concurrency) PLUS *context injection* of DAOs
  (tests swap in fakes).  ``DaoRegistry`` implements the hybrid and the
  ``/status`` page reports which mode is active.
* Exception mapping -- DAO operations raise the exception types in
  ``persistence.errors``; routes keep their original status codes
  (duplicate-email / unverified-user / empty-result are never turned into 500).
  Only infrastructure failures (connection down / timeout) map to 503 and the
  factory transparently reconnects so the process never needs a restart.
* Thread-safe DAO call counters live in ``Metrics``.
"""

from persistence.config import Settings, load_settings
from persistence.connection import ConnectionProvider, get_connection_provider
from persistence.errors import (
    DaoError,
    ConnectionUnavailable,
    DaoTimeout,
)
from persistence.daos import NoteDao, MessageDao, UserDao
from persistence.registry import DaoRegistry, get_registry, dao_registry

__all__ = [
    "Settings",
    "load_settings",
    "ConnectionProvider",
    "get_connection_provider",
    "DaoError",
    "ConnectionUnavailable",
    "DaoTimeout",
    "UserDao",
    "NoteDao",
    "MessageDao",
    "DaoRegistry",
    "get_registry",
    "dao_registry",
]
