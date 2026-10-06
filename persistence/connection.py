"""Mongo connection factory: process-wide lazy singleton with self-healing.

Concurrency
-----------
``get_client`` uses double-checked locking: N threads racing on the first
request create exactly one ``MongoClient``.  The client itself is backed by
PyMongo's thread-safe connection pool, so no per-request reconnect happens.

Recovery without restart
------------------------
If the server is down (``ServerSelectionTimeoutError``) or an operation times
out (``ExecutionTimeout`` / socket timeout), the cached client is discarded
under the lock.  The next call builds a fresh client, so a database restart
heals the running process automatically.

Testability
-----------
``client_factory`` is an injectable callable and
``reset_for_testing`` clears the singleton, which is how the test-suite swaps
in a mongomock-backed client without touching pymongo in routes.
"""

import threading

import pymongo
import pymongo.errors

from persistence.config import Settings, load_settings
from persistence.errors import ConnectionUnavailable, DaoTimeout


RECOVERABLE_ERRORS = (
    pymongo.errors.ConnectionFailure,
    pymongo.errors.ServerSelectionTimeoutError,
    pymongo.errors.NetworkTimeout,
)

TIMEOUT_ERRORS = (
    pymongo.errors.ExecutionTimeout,
    pymongo.errors.NetworkTimeout,
    pymongo.errors.WTimeoutError,
)


def default_client_factory(settings):
    """Build a production MongoClient with bounded connect/op timeouts."""
    return pymongo.MongoClient(
        settings.mongo_uri,
        serverSelectionTimeoutMS=settings.connect_timeout_ms,
        connectTimeoutMS=settings.connect_timeout_ms,
        socketTimeoutMS=settings.server_timeout_ms,
        timeoutMS=settings.server_timeout_ms,
    )


class ConnectionProvider:
    """Owns the singleton client, the reconnect lock and connection metrics."""

    def __init__(self, settings=None, client_factory=None):
        self._settings = settings if settings is not None else load_settings()
        self._client_factory = client_factory or default_client_factory
        self._lock = threading.RLock()
        self._client = None
        self._connect_attempts = 0
        self._connect_failures = 0
        self._reconnects = 0

    @property
    def settings(self):
        return self._settings

    def get_client(self):
        """Return the shared client, creating it once (thread-safe)."""
        client = self._client
        if client is not None:
            return client
        with self._lock:
            if self._client is None:
                first_attempt = self._connect_attempts == 0
                self._connect_attempts += 1
                try:
                    client = self._client_factory(self._settings)
                except RECOVERABLE_ERRORS as exc:
                    self._connect_failures += 1
                    raise ConnectionUnavailable(str(exc)) from exc
                if not first_attempt:
                    self._reconnects += 1
                self._client = client
            return self._client

    def get_database(self):
        """Return the configured database handle."""
        return self.get_client()[self._settings.mongo_dbname]

    def invalidate(self, reason=""):
        """Drop the cached client after a fatal transport error."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None
                self._reconnects += 1

    def mark_failure_and_retry(self):
        """Invalidate the dead client and hand back a fresh one."""
        self.invalidate(reason="operation failure")
        return self.get_client()

    def ping(self):
        """Short health probe used by the status page."""
        try:
            self.get_database().command("ping")
            return True
        except RECOVERABLE_ERRORS:
            return False
        except Exception:
            return False

    def reset_for_testing(self):
        """Forget the singleton client (tests only)."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._connect_attempts = 0
            self._connect_failures = 0
            self._reconnects = 0

    def stats(self):
        with self._lock:
            return {
                "client_connected": self._client is not None,
                "connect_attempts": self._connect_attempts,
                "connect_failures": self._connect_failures,
                "reconnects": self._reconnects,
                "dbname": self._settings.mongo_dbname,
            }


_provider_lock = threading.Lock()
_default_provider = None


def get_connection_provider(settings=None, client_factory=None):
    """Return the process-wide provider (module-level singleton)."""
    global _default_provider
    provider = _default_provider
    if provider is not None and settings is None and client_factory is None:
        return provider
    with _provider_lock:
        if _default_provider is None:
            _default_provider = ConnectionProvider(
                settings=settings, client_factory=client_factory
            )
        return _default_provider


def reset_default_provider():
    """Tear down the global provider (tests only)."""
    global _default_provider
    with _provider_lock:
        if _default_provider is not None:
            _default_provider.reset_for_testing()
        _default_provider = None
