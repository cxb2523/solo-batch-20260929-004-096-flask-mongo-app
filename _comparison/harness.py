"""Run baseline or refactored app against the SAME mongomock in-memory data.

Usage:  python _comparison/harness.py baseline|refactored PORT

This exists only for the before/after curl comparison; no production code
depends on it.
"""

import os
import sys

import bcrypt
import mongomock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "_comparison", "baseline")


_CLIENT = None


def fake_client_factory(*args, **kwargs):
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = mongomock.MongoClient()
    return _CLIENT


def seed(client):
    db = client["appdb"]
    db.users.delete_many({})
    db.notes.delete_many({})
    db.messages.delete_many({})
    password_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    db.users.insert_one(
        {
            "id": "u1",
            "title": "Ms",
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "verified": False,
            "password": password_hash,
        }
    )


def _fake_registration_email(user):
    return None


def _fake_message_email(from_user=None, to_user=None, message=None):
    return None


def serve_baseline(port):
    sys.path.insert(0, BASELINE)
    os.chdir(BASELINE)
    os.environ["MONGODB_URI"] = "mongodb://localhost:27017/appdb"
    os.environ["MONGO_DBNAME"] = "appdb"

    import flask_pymongo

    flask_pymongo.MongoClient = lambda *a, **k: fake_client_factory()

    # The baseline's run.py calls app.run() at module top level (no
    # __main__ guard), so exec only the code above that block.
    source_path = os.path.join(BASELINE, "run.py")
    source = open(source_path, encoding="utf-8").read()
    marker = "# Heroku environment"
    source = source.split(marker)[0]
    namespace = {"__name__": "run", "__file__": source_path}
    exec(compile(source, source_path, "exec"), namespace)

    app = namespace["app"]

    # Test harness only: disable CSRF + outbound email, seed data.
    app.config["WTF_CSRF_ENABLED"] = False
    namespace["talisman"].force_https = False
    namespace["send_registration_email"] = _fake_registration_email
    namespace["send_message_email"] = _fake_message_email
    seed(fake_client_factory())
    app.run(
        host="127.0.0.1", port=port, debug=False, use_reloader=False
    )


def serve_refactored(port):
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    os.environ["MONGODB_URI"] = "mongodb://localhost:27017/appdb"
    os.environ["MONGO_DBNAME"] = "appdb"
    os.environ["APP_CSRF_ENABLED"] = "0"
    os.environ["APP_FORCE_HTTPS"] = "0"

    # Build the app with an injected mongomock-backed provider (no pymongo
    # monkeypatching needed; this also exercises the factory-injection seam).
    from persistence.config import load_settings
    from persistence.connection import ConnectionProvider

    import run

    provider = ConnectionProvider(
        settings=load_settings(),
        client_factory=lambda settings: fake_client_factory(),
    )
    app = run.create_app(provider=provider)

    run.send_registration_email = _fake_registration_email
    run.send_message_email = _fake_message_email
    seed(fake_client_factory())
    app.run(
        host="127.0.0.1", port=port, debug=False, use_reloader=False
    )


if __name__ == "__main__":
    mode = sys.argv[1]
    port = int(sys.argv[2])
    if mode == "baseline":
        serve_baseline(port)
    else:
        serve_refactored(port)
