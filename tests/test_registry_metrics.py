"""Context injection, per-thread isolation and thread-safe call counters."""

import threading

import pytest

from persistence.connection import ConnectionProvider
from persistence.metrics import Metrics
from persistence.registry import DaoRegistry

from tests.fakes import FakeUserDao, FakeNoteDao, FakeMessageDao


def _registry():
    return DaoRegistry(provider=object())


def test_daos_are_module_singletons_by_default(stores):
    registry = _registry()
    assert registry.active_mode() == "module-singleton"

    class StubProvider:
        settings = type("S", (), {"mongo_dbname": "db"})()

        def get_database(self):
            raise AssertionError("methods must not be called")

    registry.bind_provider(StubProvider())
    first = registry.get_user_dao()
    second = registry.get_user_dao()
    assert first is second
    assert registry.get_note_dao() is not first


def test_global_override_is_reported_as_context_mode(stores, metrics):
    registry = _registry()
    fake = FakeUserDao(stores["users"], metrics)
    registry.set_global_overrides(user=fake)
    try:
        assert registry.active_mode() == "context-injected (global)"
        assert registry.get_user_dao() is fake
    finally:
        registry.clear_global_overrides()
    assert registry.active_mode() == "module-singleton"


def test_context_override_is_scoped(stores, metrics):
    registry = _registry()
    fake_a = FakeUserDao(stores["users"], metrics)
    seen = {}

    def use(name, fake):
        token = registry.push_overrides(user=fake)
        try:
            seen[name] = registry.get_user_dao()
            assert registry.active_mode() == "context-injected"
        finally:
            registry.reset_overrides(token)

    t1 = threading.Thread(target=use, args=("a", fake_a))

    class OtherFake(FakeUserDao):
        pass

    fake_b = OtherFake(stores["users"], metrics)
    t2 = threading.Thread(target=use, args=("b", fake_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert seen["a"] is fake_a
    assert seen["b"] is fake_b
    assert registry.active_mode() == "module-singleton"


def test_override_context_manager(stores, metrics):
    registry = _registry()
    fake = FakeUserDao(stores["users"], metrics)
    with registry.override_daos(user=fake):
        assert registry.get_user_dao() is fake
    assert registry.active_mode() == "module-singleton"


def test_metrics_are_thread_safe(stores):
    metrics = Metrics()

    def hammer():
        for _ in range(1000):
            metrics.record("UserDao.find_one")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snapshot = metrics.snapshot()
    assert snapshot["total_calls"] == 8000
    assert snapshot["calls"]["UserDao.find_one"] == 8000


def test_dao_counters_count_every_method(stores, metrics):
    user_dao = FakeUserDao(stores["users"], metrics)
    note_dao = FakeNoteDao(stores["notes"], metrics)
    user_dao.insert({"id": "u1", "email": "a@b.c"})
    user_dao.find_by_email("a@b.c")
    user_dao.find_by_email("missing@x.y")
    note_dao.insert({"id": "n1", "user_id": "u1", "deleted": False})
    note_dao.active_for_user("u1")
    note_dao.active_for_user("nobody")
    snapshot = metrics.snapshot()
    assert snapshot["calls"]["FakeUserDao.insert_one"] == 1
    assert snapshot["calls"]["FakeUserDao.find_one"] == 2
    assert snapshot["calls"]["FakeNoteDao.insert_one"] == 1
    assert snapshot["calls"]["FakeNoteDao.find"] == 2
    assert snapshot["total_failures"] == 0
