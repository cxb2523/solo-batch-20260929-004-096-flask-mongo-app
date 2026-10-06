"""DAO registry: module-level singletons by default, swappable for testing.

Lookup order for ``get_user_dao`` etc.:

1. a DAO installed through ``override_daos`` (a contextvar-backed *context
   injection*, correct with pooled worker threads);
2. a global test override (``set_global_overrides``);
3. the lazily built real DAO backed by the process-wide MongoClient singleton
   -- *module-level singleton*.
"""

import threading
from contextlib import contextmanager
from contextvars import ContextVar

from persistence.daos import MessageDao, NoteDao, UserDao


DAO_NAMES = ("user", "note", "message")


class DaoRegistry:
    def __init__(self, provider=None):
        self._provider = provider
        self._singleton_lock = threading.Lock()
        self._singletons = {}
        self._override_lock = threading.RLock()
        self._global_overrides = {}
        self._context_overrides = ContextVar(
            "dao_context_overrides", default={}
        )

    # -- wiring ----------------------------------------------------------

    def bind_provider(self, provider):
        self._provider = provider

    def _build_real(self, name):
        cls = {"user": UserDao, "note": NoteDao, "message": MessageDao}[name]
        return cls(provider=self._provider)

    def get(self, name):
        context = self._context_overrides.get()
        if name in context:
            return context[name]
        with self._override_lock:
            global_override = self._global_overrides.get(name)
        if global_override is not None:
            return global_override
        singleton = self._singletons.get(name)
        if singleton is not None:
            return singleton
        with self._singleton_lock:
            if name not in self._singletons:
                self._singletons[name] = self._build_real(name)
            return self._singletons[name]

    def get_user_dao(self):
        return self.get("user")

    def get_note_dao(self):
        return self.get("note")

    def get_message_dao(self):
        return self.get("message")

    # -- test seams ------------------------------------------------------

    def set_global_overrides(self, **daos):
        with self._override_lock:
            for name, dao in daos.items():
                if name not in DAO_NAMES:
                    raise KeyError(f"unknown DAO: {name}")
                self._global_overrides[name] = dao

    def clear_global_overrides(self):
        with self._override_lock:
            self._global_overrides.clear()

    def push_overrides(self, **daos):
        """Return a reset token for contextvar fakes (use with reset_overrides)."""
        for name in daos:
            if name not in DAO_NAMES:
                raise KeyError(f"unknown DAO: {name}")
        current = dict(self._context_overrides.get())
        token = self._context_overrides.set({**current, **daos})
        return token

    def reset_overrides(self, token):
        self._context_overrides.reset(token)

    @contextmanager
    def override_daos(self, **daos):
        """Context manager: fakes visible only inside the ``with`` block."""
        token = self.push_overrides(**daos)
        try:
            yield
        finally:
            self.reset_overrides(token)

    def reset(self):
        """Drop every override and cached singleton (tests only)."""
        with self._override_lock:
            self._global_overrides.clear()
        self._context_overrides.set({})
        with self._singleton_lock:
            self._singletons.clear()

    # -- introspection (/status) ----------------------------------------

    def active_mode(self):
        """``context-injected`` when any fake is installed, else singleton."""
        if self._context_overrides.get():
            return "context-injected"
        with self._override_lock:
            if self._global_overrides:
                return "context-injected (global)"
        return "module-singleton"

    def status_snapshot(self):
        """Per-DAO call counts plus which implementation answers each name."""
        per_dao = {}
        total_calls = 0
        total_failures = 0
        for name in DAO_NAMES:
            dao = self.get(name)
            stats = dao.metrics.snapshot()
            total_calls += stats["total_calls"]
            total_failures += stats["total_failures"]
            per_dao[name] = {
                "class": dao.dao_name(),
                "module": dao.__class__.__module__,
                "collection": getattr(dao, "collection_name", None),
                "calls": stats["calls"],
                "failures": stats["failures"],
                "total_calls": stats["total_calls"],
                "total_failures": stats["total_failures"],
            }
        return {
            "connection_mode": self.active_mode(),
            "daos": per_dao,
            "dao_total_calls": total_calls,
            "dao_total_failures": total_failures,
        }


dao_registry = DaoRegistry()


def get_registry():
    return dao_registry
