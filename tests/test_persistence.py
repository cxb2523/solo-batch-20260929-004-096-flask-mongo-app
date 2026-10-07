import re
import threading

import mongomock
import pytest
from pymongo.errors import ConnectionFailure

from persistence import (
    ConnectionFactory,
    UserDAO,
    NoteDAO,
    MessageDAO,
    PersistenceError,
    DuplicateEmailError,
)


class FakeClient:
    # Minimal stand-in for MongoClient
    def __init__(self, *args, **kwargs):
        self.closed = False
        self.admin = self

    def command(self, name):
        return {"ok": 1.0}

    def __getitem__(self, name):
        return {}

    def close(self):
        self.closed = True


def test_client_created_once_under_concurrency():
    creations = []

    def counting_factory(*args, **kwargs):
        client = FakeClient()
        creations.append(client)
        return client

    factory = ConnectionFactory(uri="mongodb://fake/", client_factory=counting_factory)

    results = []
    def worker():
        results.append(factory.get_client())

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(creations) == 1
    assert factory.connect_count == 1
    assert all(client is creations[0] for client in results)


def test_recovers_from_connection_failure_without_restart():
    attempts = []

    def flaky_factory(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionFailure("mongo is down")
        return FakeClient()

    factory = ConnectionFactory(uri="mongodb://fake/", client_factory=flaky_factory)

    with pytest.raises(PersistenceError):
        factory.get_client()

    # Failure was not cached: next call reconnects successfully
    client = factory.get_client()
    assert isinstance(client, FakeClient)
    assert len(attempts) == 2
    assert factory.connect_count == 1


def test_reset_drops_client_and_reconnects():
    creations = []

    def counting_factory(*args, **kwargs):
        client = FakeClient()
        creations.append(client)
        return client

    factory = ConnectionFactory(uri="mongodb://fake/", client_factory=counting_factory)
    first = factory.get_client()
    factory.reset()
    assert first.closed is True
    second = factory.get_client()
    assert second is not first
    assert factory.connect_count == 2


def make_mongo_factory():
    return ConnectionFactory(
        uri="mongodb://fake/", db_name="testdb",
        client_factory=mongomock.MongoClient)


def test_dao_call_count_is_thread_safe():
    dao = UserDAO(make_mongo_factory())
    threads = [threading.Thread(
        target=lambda: [dao.find_by_email("a@b.c") for _ in range(50)])
        for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert dao.call_count == 8 * 50


def test_duplicate_email_raises_typed_error():
    factory = make_mongo_factory()
    factory.get_db()["users"].create_index("email", unique=True)
    dao = UserDAO(factory)
    dao.insert({"email": "dup@example.com", "id": "1"})
    with pytest.raises(DuplicateEmailError):
        dao.insert({"email": "dup@example.com", "id": "2"})


def test_dao_error_resets_connection_for_recovery():
    factory = make_mongo_factory()
    dao = NoteDAO(factory)
    dao.insert({"id": "n1"})
    assert factory.connect_count == 1

    # Force a failure: the DAO call must raise PersistenceError
    # and drop the cached client
    class BrokenCollection:
        def find_one(self, *a, **k):
            raise ConnectionFailure("lost connection")

    dao._collection = lambda: BrokenCollection()
    with pytest.raises(PersistenceError):
        dao._run(lambda c: c.find_one({}))
    assert factory._client is None

    # Recovery without restarting: fresh DAO call reconnects
    dao2 = NoteDAO(factory)
    dao2.insert({"id": "n2"})
    assert factory.connect_count == 2


def test_all_daos_share_one_connection():
    factory = make_mongo_factory()
    user_dao = UserDAO(factory)
    note_dao = NoteDAO(factory)
    message_dao = MessageDAO(factory)
    user_dao.find_by_email("x@y.z")
    note_dao.find_for_user("u1")
    message_dao.find_inbox("u1")
    assert factory.connect_count == 1


def test_run_py_does_not_import_pymongo():
    with open("run.py", encoding="utf-8") as f:
        source = f.read()
    assert not re.search(r"^\s*(import|from)\s+\S*pymongo", source, re.MULTILINE)
