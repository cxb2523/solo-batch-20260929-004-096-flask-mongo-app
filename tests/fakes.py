"""In-memory fakes for DAOs; used by route tests and pytest fixtures."""

import threading
from datetime import datetime

from persistence.daos import MessageDao, NoteDao, UserDao


class _Result:
    def __init__(self, acknowledged, **extra):
        self.acknowledged = acknowledged
        self.__dict__.update(extra)

    def __bool__(self):
        # Mirrors pymongo: every acknowledged result object is truthy.
        return self.acknowledged


class SortableResults(list):
    """A list with a pymongo-cursor-style chainable ``sort`` method."""

    def sort(self, key, direction=1):
        from datetime import datetime as _dt

        super().sort(
            key=lambda r: r.get(key) or _dt.min,
            reverse=direction == -1,
        )
        return self


class InMemoryCollection:
    """Subset of the pymongo collection API used by the real DAO methods."""

    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction=1):
        self._sort = (key, direction)
        return self

    def _matches(self, row, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(self._matches(row, clause) for clause in expected):
                    return False
            elif isinstance(expected, dict) and "$ne" in expected:
                if row.get(key) == expected["$ne"]:
                    return False
            elif row.get(key) != expected:
                return False
        return True

    def _matched(self, query):
        rows = [r for r in self.rows if self._matches(r, query)]
        sort = getattr(self, "_sort", None)
        if sort is not None:
            key, direction = sort
            rows.sort(
                key=lambda r: r.get(key) or datetime.min,
                reverse=direction == -1,
            )
        return rows

    def find(self, query, projection=None):
        return SortableResults([dict(r) for r in self._matched(query)])

    def find_one(self, query, projection=None):
        rows = self._matched(query)
        return dict(rows[0]) if rows else None

    def insert_one(self, document):
        self.rows.append(dict(document))
        return _Result(True, inserted_id=document.get("id"))

    def update_one(self, query, update):
        rows = self._matched(query)
        modified = 0
        if rows:
            for operator, fields in update.items():
                if operator == "$set":
                    rows[0].update(fields)
            modified = 1
        return _Result(
            True, matched_count=1 if rows else 0, modified_count=modified
        )

    def delete_one(self, query):
        rows = self._matched(query)
        deleted = 0
        if rows:
            self.rows.remove(rows[0])
            deleted = 1
        return _Result(True, deleted_count=deleted)

    def delete_many(self, query):
        rows = self._matched(query)
        for row in rows:
            if row in self.rows:
                self.rows.remove(row)
        return _Result(True, deleted_count=len(rows))


class InMemoryRunnerMixin:
    """Replaces BaseDao.run so real DAO methods execute against memory."""

    def bind_store(self, store, metrics):
        self._store = store
        self._metrics = metrics
        self._mem_lock = threading.Lock()

    def _collection(self, client=None):
        return InMemoryCollection(self._store)

    def run(self, method, *args, _retry_ok=None, **kwargs):
        label = f"{self.dao_name()}.{method}"
        with self._mem_lock:
            try:
                result = getattr(self._collection(), method)(*args, **kwargs)
            except Exception:
                self._metrics.record(label, success=False)
                raise
            self._metrics.record(label, success=True)
            return result


class FakeUserDao(InMemoryRunnerMixin, UserDao):
    def __init__(self, store, metrics):
        self.bind_store(store, metrics)


class FakeNoteDao(InMemoryRunnerMixin, NoteDao):
    def __init__(self, store, metrics):
        self.bind_store(store, metrics)


class FakeMessageDao(InMemoryRunnerMixin, MessageDao):
    def __init__(self, store, metrics):
        self.bind_store(store, metrics)
