import json

import mongomock
import pytest

import run
from persistence import ConnectionFactory, UserDAO, NoteDAO, MessageDAO
from verification import generate_confirmation_token

BASE = "https://localhost"


@pytest.fixture()
def client(monkeypatch):
    factory = ConnectionFactory(
        uri="mongodb://fake/", db_name="testdb",
        client_factory=mongomock.MongoClient)
    user_dao = UserDAO(factory)
    note_dao = NoteDAO(factory)
    message_dao = MessageDAO(factory)
    monkeypatch.setattr(run, "factory", factory)
    monkeypatch.setattr(run, "user_dao", user_dao)
    monkeypatch.setattr(run, "note_dao", note_dao)
    monkeypatch.setattr(run, "message_dao", message_dao)
    monkeypatch.setattr(run, "send_registration_email", lambda *a, **k: None)
    monkeypatch.setattr(run, "send_message_email", lambda *a, **k: None)
    run.app.config["WTF_CSRF_ENABLED"] = False
    with run.app.test_client() as client:
        client.dao_calls = lambda: {
            "user": user_dao.call_count,
            "note": note_dao.call_count,
            "message": message_dao.call_count,
        }
        yield client


def register(client, email="user@example.com", password="secret"):
    return client.post("/register", base_url=BASE, data={
        "email": email, "title": "Mr", "first_name": "Test",
        "last_name": "User", "pass": password,
    })


def test_register_success_redirects_to_profile(client):
    response = register(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile")


def test_register_duplicate_email_redirects_error_1(client):
    register(client)
    client.get("/logout", base_url=BASE)
    response = register(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/register?error=1")


def test_login_wrong_password_redirects_error_1(client):
    register(client)
    client.get("/logout", base_url=BASE)
    response = client.post("/login", base_url=BASE, data={
        "email": "user@example.com", "pass": "wrong"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?error=1")


def test_login_success_redirects_to_profile(client):
    register(client)
    client.get("/logout", base_url=BASE)
    response = client.post("/login", base_url=BASE, data={
        "email": "user@example.com", "pass": "secret"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile")


def test_profile_requires_login(client):
    response = client.get("/profile", base_url=BASE)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_profile_with_zero_notes_is_200(client):
    register(client)
    response = client.get("/profile", base_url=BASE)
    assert response.status_code == 200


def test_messages_page_empty_is_200(client):
    register(client)
    response = client.get("/messages", base_url=BASE)
    assert response.status_code == 200


def test_delete_note_success_message(client):
    register(client)
    response = client.post("/delete_note", base_url=BASE,
                           data={"note_id": "n1"})
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "Success! Note deleted"


def test_delete_account_returns_json_flags(client):
    register(client)
    response = client.post("/delete_account", base_url=BASE)
    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload == {"user_deleted": True, "notes_deleted": True,
                       "messages_deleted": True}


def test_confirm_with_bad_token_is_200_not_500(client):
    response = client.get("/confirm/not-a-token", base_url=BASE)
    assert response.status_code == 200


def test_confirm_with_valid_token_marks_verified(client):
    register(client)
    token = generate_confirmation_token("user@example.com")
    response = client.get("/confirm/" + token, base_url=BASE)
    assert response.status_code == 200
    user = run.user_dao.find_by_email("user@example.com")
    assert user["verified"] is True


def test_status_page_reports_mode_and_dao_calls(client):
    register(client)
    client.get("/profile", base_url=BASE)
    response = client.get("/status", base_url=BASE)
    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload["status"] == "ok"
    assert payload["connection_mode"] == "module_singleton"
    assert payload["connect_count"] == 1
    assert set(payload["dao_calls"]) == {"user", "note", "message"}
    assert payload["dao_calls"] == client.dao_calls()
    assert payload["dao_calls"]["user"] > 0
    assert payload["dao_calls"]["note"] > 0
