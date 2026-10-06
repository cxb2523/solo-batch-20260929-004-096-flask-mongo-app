# Template for a basic web app using Python Flask and MongoDB

This is a template for a web app using Flask and MongoDB. It uses Flask-Login for authentication, Flask-Pymongo for the database connection, Flask-Bcrypt for password hashing and Flask-Talisman for security. The front end uses Bootstrap for styling. The app features user verification by email, basic note recording and messaging between users with email notifications.

## How to use the template

Simply insert your MongoDB database URI and database name in the ```configuration.ini``` file. You can also add SMTP server login details to support sending registration emails and message notifications to users.

Run the app using the terminal command: ```python run.py```

## Live demo

The app is live for demonstration purposes at [flask-mongodb-demo.chriswilson.app](https://flask-mongodb-demo.chriswilson.app).

## Persistence layer

All database access lives in the `persistence/` package; `run.py` routes only
call DAOs and never import `pymongo`.

* `persistence/connection.py` - connection factory with a process-wide,
  double-checked-locked `MongoClient` singleton (no reconnect storms under
  concurrency) and transparent self-healing: connection failures/timeouts
  invalidate the dead client and the next request reconnects, without a
  process restart. Reads retry once; writes are never blindly retried.
* `persistence/daos/` - `UserDao`, `NoteDao`, `MessageDao`, each recording
  thread-safe call counts (`persistence/metrics.py`).
* `persistence/registry.py` - defaults to module-singleton DAOs but supports
  contextvar-scoped or global overrides, so tests inject fake DAOs without
  touching a real database. The active mode is reported on `/status`.
* Infrastructure failures map to HTTP 503; business outcomes keep their
  original per-endpoint status codes (duplicate email 302 with `?error=1`,
  unverified-user flows 200, empty results are data not errors; a missing
  message recipient returns 404 instead of the previous 500 crash).

`GET /status` returns an HTML page (or JSON with `?format=json`) showing
`connection_mode`, connection counters (`connect_attempts`,
`connect_failures`, `reconnects`, `mongo_ping_ok`) and per-DAO call counts.

## Tests

```
pytest
```

The suite covers the singleton-under-concurrency guarantee, self-healing,
write-timeout safety, thread-safe counters, fake-DAO context injection and
per-endpoint status code parity.
