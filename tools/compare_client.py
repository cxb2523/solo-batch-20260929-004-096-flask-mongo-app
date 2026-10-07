# Drives the Flask test client against a given copy of the app and prints
# status codes / bodies for endpoints that do not require a live MongoDB.
# Usage: python tools/compare_client.py <app_dir>
import os
import sys

app_dir = os.path.abspath(sys.argv[1])
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/")
os.environ.setdefault("MONGO_DBNAME", "sample")

# Prevent the app from starting a real server on import (baseline run.py
# calls app.run() at module level)
from flask import Flask
Flask.run = lambda self, *a, **k: None

os.chdir(app_dir)
sys.path.insert(0, app_dir)

import run

BASE = "https://localhost"
client = run.app.test_client()

checks = [
    ("GET", "/"),
    ("GET", "/login"),
    ("GET", "/register"),
    ("GET", "/confirm/badtoken"),
    ("GET", "/profile"),
    ("GET", "/messages"),
    ("GET", "/logout"),
    ("GET", "/status"),
    ("POST", "/login"),
    ("POST", "/register"),
    ("POST", "/add_note"),
    ("POST", "/delete_account"),
]

for method, path in checks:
    response = client.open(path, method=method, base_url=BASE)
    location = response.headers.get("Location", "")
    body = response.get_data(as_text=True)
    print("%-4s %-18s -> %s %s %s" % (
        method, path, response.status_code, location, body[:60].replace("\n", " ")))
