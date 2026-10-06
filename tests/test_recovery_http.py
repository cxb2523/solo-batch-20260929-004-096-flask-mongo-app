"""503 mapping and process-alive recovery through the connection provider."""

import pymongo.errors

from persistence.config import Settings
from persistence.connection import ConnectionProvider
from persistence.errors import ConnectionUnavailable


def _settings():
    return Settings(
        secret_key="k",
        security_password_salt="s",
        mongo_uri="mongodb://localhost:27017",
        mongo_dbname="appdb",
        server_timeout_ms=100,
        connect_timeout_ms=100,
    )


class HealthyClient:
    def __getitem__(self, dbname):
        class Db:
            def __getitem__(self_, name):
                class Col:
                    def find_one(self__, *a, **k):
                        return None

                return Col()

            def command(self_, name):
                return {"ok": 1}

        return Db()

    def close(self):
        pass


class DownClient(HealthyClient):
    def __getitem__(self, dbname):
        class DeadDb:
            def __getitem__(self_, name):
                class DeadCol:
                    def find_one(self__, *a, **k):
                        raise pymongo.errors.ServerSelectionTimeoutError(
                            "down"
                        )

                return DeadCol()

            def command(self_, name):
                raise pymongo.errors.ServerSelectionTimeoutError("down")

        return DeadDb()


def test_http_503_then_recovers_without_restart(monkeypatch):
    import importlib
    import os

    os.environ["APP_CSRF_ENABLED"] = "0"
    os.environ["APP_FORCE_HTTPS"] = "0"

    state = {"down": True}

    def factory(settings):
        return DownClient() if state["down"] else HealthyClient()

    provider = ConnectionProvider(
        settings=_settings(), client_factory=factory
    )

    import run

    importlib.reload(run)
    app = run.create_app(settings=_settings(), provider=provider)
    client = app.test_client()

    # Database is down -> reads fail with 503, process stays up.
    response = client.post(
        "/login", data={"email": "a@b.c", "pass": "x"}
    )
    assert response.status_code == 503

    # "DBA restarts Mongo"; the next request self-heals on a fresh client.
    state["down"] = False
    response = client.post(
        "/login", data={"email": "a@b.c", "pass": "x"}
    )
    assert response.status_code == 302
    assert "/login?error=1" in response.headers["Location"]
    assert provider.stats()["reconnects"] >= 1
