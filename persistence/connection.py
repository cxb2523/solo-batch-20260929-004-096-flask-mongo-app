import os
import threading

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .exceptions import PersistenceError

DEFAULT_URI = "mongodb://localhost:27017/"
DEFAULT_DB_NAME = "sample"
SERVER_SELECTION_TIMEOUT_MS = 5000


class ConnectionFactory:
    # Thread-safe lazy factory for a single shared MongoClient.
    #
    # - The client is created at most once, even under concurrency
    #   (double-checked locking), so concurrent requests never reconnect.
    # - If connecting/ping fails, nothing is cached: the next call retries,
    #   so a broken connection recovers without restarting the process.
    # - Tests can inject client_factory (e.g. mongomock.MongoClient) to get
    #   a fake implementation.

    def __init__(self, uri=None, db_name=None, client_factory=None):
        self._uri = uri or os.environ.get("MONGODB_URI") or DEFAULT_URI
        self._db_name = db_name or os.environ.get("MONGO_DBNAME") or DEFAULT_DB_NAME
        self._client_factory = client_factory or MongoClient
        self._client = None
        self._lock = threading.Lock()
        self._connect_count = 0

    @property
    def mode(self):
        return "module_singleton"

    @property
    def connect_count(self):
        # Number of times a client was actually created
        with self._lock:
            return self._connect_count

    def get_client(self):
        client = self._client
        if client is None:
            with self._lock:
                if self._client is None:
                    try:
                        client = self._client_factory(
                            self._uri,
                            serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
                        client.admin.command("ping")
                    except PyMongoError as exc:
                        raise PersistenceError(
                            "cannot connect to MongoDB: %s" % exc) from exc
                    self._client = client
                    self._connect_count += 1
                client = self._client
        return client

    def get_db(self):
        return self.get_client()[self._db_name]

    def reset(self):
        # Drop the cached client so the next call reconnects
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None


_factory = None
_factory_lock = threading.Lock()


def get_factory():
    # Module-level singleton factory used by the app
    global _factory
    if _factory is None:
        with _factory_lock:
            if _factory is None:
                _factory = ConnectionFactory()
    return _factory


def set_factory(factory):
    # Replace the module-level factory (tests / fake implementations)
    global _factory
    with _factory_lock:
        _factory = factory


def reset_factory():
    global _factory
    with _factory_lock:
        _factory = None
