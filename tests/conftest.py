"""Shared fixtures: in-memory fake DAOs and a CSRF-free Flask test client."""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from persistence.metrics import Metrics
from tests.fakes import FakeMessageDao, FakeNoteDao, FakeUserDao


@pytest.fixture
def stores():
    return {"users": [], "notes": [], "messages": []}


@pytest.fixture
def metrics():
    return Metrics()


@pytest.fixture
def fake_daos(stores, metrics):
    return {
        "user": FakeUserDao(stores["users"], metrics),
        "note": FakeNoteDao(stores["notes"], metrics),
        "message": FakeMessageDao(stores["messages"], metrics),
    }


@pytest.fixture
def app(fake_daos):
    os.environ["APP_CSRF_ENABLED"] = "0"
    os.environ["APP_FORCE_HTTPS"] = "0"
    os.environ["MONGO_DBNAME"] = "testdb"
    os.environ["MONGODB_URI"] = "mongodb://localhost:27017/testdb"
    import run as run_module

    importlib.reload(run_module)
    application = run_module.app
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    registry = run_module.get_registry()
    token = registry.push_overrides(**fake_daos)
    try:
        yield application
    finally:
        registry.reset_overrides(token)


@pytest.fixture
def client(app):
    return app.test_client()
