"""Connection factory: singleton under concurrency + self-healing."""

import threading

import pytest
import pymongo.errors

from persistence.connection import ConnectionProvider
from persistence.errors import ConnectionUnavailable, DaoTimeout
from persistence.config import Settings


def _settings():
    return Settings(
        secret_key="k",
        security_password_salt="s",
        mongo_uri="mongodb://localhost:27017",
        mongo_dbname="testdb",
        server_timeout_ms=100,
        connect_timeout_ms=100,
    )


class FakeClient:
    def __init__(self, settings=None):
        self.settings = settings
        self.closed = False

    def __getitem__(self, dbname):
        return FakeDatabase()

    def close(self):
        self.closed = True


class FakeDatabase:
    def __getitem__(self, name):
        return FakeCollection()

    def command(self, name):
        return {"ok": 1}


class FakeCollection:
    def find_one(self, *args, **kwargs):
        return {"id": "x"}


def test_concurrent_first_access_creates_one_client():
    gate = threading.Event()
    created_clients = []
    counter_lock = threading.Lock()

    def factory(settings):
        # Hold the first (lock-holding) thread until every worker has piled
        # up on the singleton lock.
        gate.wait(timeout=5)
        client = FakeClient(settings)
        with counter_lock:
            created_clients.append(client)
        return client

    provider = ConnectionProvider(settings=_settings(), client_factory=factory)
    barrier = threading.Barrier(20)
    results = []

    def worker():
        barrier.wait()
        results.append(provider.get_client())

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    # Give all 20 threads time to enter get_client(), then unblock the
    # single one that wins the lock.
    threading.Event().wait(0.2)
    gate.set()
    for t in threads:
        t.join(timeout=5)

    assert len(created_clients) == 1
    assert len(results) == 20
    assert all(client is created_clients[0] for client in results)
    stats = provider.stats()
    assert stats["connect_attempts"] == 1
    assert stats["client_connected"] is True
    # Subsequent access keeps returning the same client (no reconnects).
    assert provider.get_client() is created_clients[0]
    assert provider.stats()["connect_attempts"] == 1


def test_first_connect_failure_does_not_cache_broken_client():
    calls = {"n": 0}

    def factory(settings):
        calls["n"] += 1
        raise pymongo.errors.ServerSelectionTimeoutError("boom")

    provider = ConnectionProvider(settings=_settings(), client_factory=factory)
    with pytest.raises(ConnectionUnavailable):
        provider.get_client()
    with pytest.raises(ConnectionUnavailable):
        provider.get_client()
    assert calls["n"] == 2  # retried without a process restart
    assert provider.stats()["connect_failures"] == 2


def test_transport_error_invalidates_client_and_next_call_reconnects():
    sequence = {"fail_collections": 0}

    class FlakyCollection(FakeCollection):
        def find_one(self, *args, **kwargs):
            raise pymongo.errors.ConnectionFailure("socket closed")

    class FlakyDatabase(FakeDatabase):
        def __getitem__(self, name):
            if sequence["fail_collections"] == 0:
                sequence["fail_collections"] += 1
                return FlakyCollection()
            return FakeCollection()

    class FlakyClient(FakeClient):
        def __getitem__(self, dbname):
            return FlakyDatabase()

    provider = ConnectionProvider(
        settings=_settings(), client_factory=lambda s: FlakyClient(s)
    )

    from persistence.daos import UserDao

    dao = UserDao(provider=provider)
    # Read transparently retries once on a fresh client and succeeds.
    result = dao.find_by_id("x")
    assert result == {"id": "x"}
    assert provider.stats()["reconnects"] >= 1
    assert provider.ping() is True


def test_write_timeout_is_not_retried_but_connection_is_reset():
    state = {"fail_once": True, "writes": 0}

    class TimeoutOnceCollection(FakeCollection):
        def insert_one(self, document):
            state["writes"] += 1
            if state["fail_once"]:
                raise pymongo.errors.NetworkTimeout("slow")
            return _Ack()

    class _Ack:
        acknowledged = True

    class TimeoutDatabase(FakeDatabase):
        def __getitem__(self, name):
            return TimeoutOnceCollection()

    class TimeoutClient(FakeClient):
        def __getitem__(self, dbname):
            return TimeoutDatabase()

    provider = ConnectionProvider(
        settings=_settings(), client_factory=lambda s: TimeoutClient(s)
    )
    from persistence.daos import NoteDao

    dao = NoteDao(provider=provider)
    with pytest.raises(DaoTimeout):
        dao.insert({"id": "n1"})
    assert state["writes"] == 1  # no double insert
    assert provider.stats()["client_connected"] is False  # reset for recovery
    # Next request heals automatically (fresh client, insert succeeds).
    state["fail_once"] = False
    assert bool(dao.insert({"id": "n2"})) is True
    assert state["writes"] == 2


def test_status_snapshot_reports_mode_and_counts():
    provider = ConnectionProvider(
        settings=_settings(), client_factory=lambda s: FakeClient(s)
    )
    from persistence.registry import DaoRegistry

    registry = DaoRegistry(provider=provider)
    registry.get_user_dao().find_by_email("a@b.c")
    snapshot = registry.status_snapshot()
    assert snapshot["connection_mode"] == "module-singleton"
    assert snapshot["dao_total_calls"] == 1
    assert snapshot["daos"]["user"]["total_calls"] == 1
