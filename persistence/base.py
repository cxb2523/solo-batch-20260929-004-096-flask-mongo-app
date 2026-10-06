"""Base DAO: collection access, retries, exception translation and counting."""

import pymongo.errors

from persistence.connection import (
    RECOVERABLE_ERRORS,
    TIMEOUT_ERRORS,
)
from persistence.errors import ConnectionUnavailable, DaoTimeout
from persistence.metrics import Metrics

READ_METHODS = frozenset(
    {"find", "find_one", "count_documents", "estimated_document_count",
     "aggregate", "distinct"}
)


class BaseDao:
    """Wraps one Mongo collection; subclasses only name it."""

    collection_name = None

    def __init__(self, provider=None, metrics=None):
        # Imported lazily so a fake DAO never needs a real provider.
        if provider is None:
            from persistence.connection import get_connection_provider

            provider = get_connection_provider()
        self._provider = provider
        self._metrics = metrics if metrics is not None else Metrics()

    @property
    def metrics(self):
        return self._metrics

    def dao_name(self):
        return self.__class__.__name__

    def _collection(self, client=None):
        if client is None:
            database = self._provider.get_database()
        else:
            database = client[self._provider.settings.mongo_dbname]
        return database[self.collection_name]

    def run(self, method, *args, _retry_ok=None, **kwargs):
        """Execute one collection operation with one transparent reconnect.

        Empty results are normal data and never raise; only transport errors
        are translated so routes can map them to 503.  Only idempotent reads
        are retried transparently -- an ambiguous write timeout could
        otherwise double-insert; the write invalidates the dead client and
        raises 503, and the very next request heals on a fresh client.
        """
        label = f"{self.dao_name()}.{method}"
        can_retry = method in READ_METHODS if _retry_ok is None else _retry_ok
        try:
            result = getattr(self._collection(), method)(*args, **kwargs)
            self._metrics.record(label, success=True)
            return result
        except TIMEOUT_ERRORS as exc:
            self._metrics.record(label, success=False)
            if can_retry:
                self._provider.invalidate("timeout")
                return self.run(method, *args, _retry_ok=False, **kwargs)
            self._provider.invalidate("timeout")
            raise DaoTimeout(str(exc)) from exc
        except RECOVERABLE_ERRORS as exc:
            self._metrics.record(label, success=False)
            if can_retry:
                self._provider.invalidate("connection lost")
                return self.run(method, *args, _retry_ok=False, **kwargs)
            self._provider.invalidate("connection lost")
            raise ConnectionUnavailable(str(exc)) from exc
        except pymongo.errors.PyMongoError as exc:
            self._metrics.record(label, success=False)
            # Unexpected DB error: bubble up unchanged; routes treat it as a
            # normal request failure rather than hiding it as empty data.
            raise
