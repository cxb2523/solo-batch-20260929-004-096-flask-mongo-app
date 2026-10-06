"""Per-endpoint status codes must match the pre-refactor behaviour.

Explicitly NOT 500:
* duplicate email at register -> 302 redirect ?error=1
* unverified user hitting /verify -> 200 text
* empty recipient at /send_message -> 404 (old code crashed with 500)
* Mongo down / timeout -> 503 (handled infrastructure failure)
"""

from persistence.errors import ConnectionUnavailable


from flask_bcrypt import Bcrypt


_bcrypt = Bcrypt()
PASSWORD_HASH = _bcrypt.generate_password_hash("secret").decode("utf-8")


def _user(user_id="u1", email="a@b.c", verified=False):
    return {
        "id": user_id,
        "title": "Mr",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": email,
        "verified": verified,
        "password": PASSWORD_HASH,
    }


def _login(client, email="a@b.c", password="secret"):
    return client.post(
        "/login",
        data={"email": email, "pass": password},
        follow_redirects=False,
    )


def test_index_is_200(client):
    assert client.get("/").status_code == 200


def test_login_get_200_and_bad_post_302(client):
    assert client.get("/login").status_code == 200
    response = client.post(
        "/login", data={"email": "nope@x.y", "pass": "x"}
    )
    assert response.status_code == 302
    assert "/login?error=1" in response.headers["Location"]


def test_register_duplicate_email_is_302_not_500(client, stores):
    stores["users"].append(_user(email="taken@x.y"))
    response = client.post(
        "/register",
        data={
            "email": "taken@x.y",
            "title": "Mr",
            "first_name": "A",
            "last_name": "B",
            "pass": "pw",
        },
    )
    assert response.status_code == 302
    assert "/register?error=1" in response.headers["Location"]


def test_register_new_user_302_to_profile(client, stores, monkeypatch):
    monkeypatch.setattr("run.send_registration_email", lambda user: None)
    response = client.post(
        "/register",
        data={
            "email": "new@x.y",
            "title": "Ms",
            "first_name": "New",
            "last_name": "User",
            "pass": "pw",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile")
    assert len(stores["users"]) == 1
    assert stores["users"][0]["email"] == "new@x.y"


def test_verify_unverified_user_200_not_error(client, stores, monkeypatch):
    stores["users"].append(_user(verified=False))
    monkeypatch.setattr("run.send_registration_email", lambda user: None)
    _login(client)
    response = client.post("/verify")
    assert response.status_code == 200
    assert b"Verification email sent" in response.data


def test_verify_already_verified_200(client, stores, monkeypatch):
    stores["users"].append(_user(verified=True))
    _login(client)
    response = client.post("/verify")
    assert response.status_code == 200
    assert b"already verified" in response.data


def test_profile_empty_notes_200(client, stores):
    stores["users"].append(_user())
    _login(client)
    response = client.get("/profile")
    assert response.status_code == 200
    assert b"You have no notes" in response.data


def test_messages_empty_200(client, stores):
    stores["users"].append(_user())
    _login(client)
    response = client.get("/messages")
    assert response.status_code == 200
    assert b"no messages here" in response.data


def test_send_message_missing_recipient_is_404_not_500(client, stores):
    stores["users"].append(_user())
    _login(client)
    response = client.post(
        "/send_message", data={"title": "t", "body": "b", "user": "ghost"}
    )
    assert response.status_code == 404
    assert b"Recipient user not found" in response.data


def test_delete_account_json_200(client, stores, monkeypatch):
    import run

    stores["users"].append(_user())
    stores["notes"].append({"id": "n1", "user_id": "u1"})
    stores["messages"].append({"id": "m1", "to_id": "u1"})
    _login(client)
    response = client.post("/delete_account")
    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {
        "user_deleted": True,
        "notes_deleted": True,
        "messages_deleted": True,
    }
    assert stores["users"] == []


def test_db_outage_maps_to_503(client, fake_daos):
    class ExplodingUserDao(type(fake_daos["user"])):
        def find_by_email(self, email, include_id_field=True):
            raise ConnectionUnavailable("mongo down")

    import run

    exploding = ExplodingUserDao([], run.get_registry().get_user_dao().metrics)
    with run.get_registry().override_daos(user=exploding):
        response = client.post(
            "/login", data={"email": "a@b.c", "pass": "x"}
        )
    assert response.status_code == 503


def test_confirm_bad_token_200_failure_page(client):
    response = client.get("/confirm/not-a-real-token")
    assert response.status_code == 200
    assert b"could not be verified" in response.data


def test_status_json_fields(client):
    response = client.get("/status?format=json")
    assert response.status_code == 200
    body = response.get_json()
    assert body["connection_mode"] == "context-injected"
    for field in (
        "mongo_client_connected",
        "mongo_ping_ok",
        "mongo_db",
        "connect_attempts",
        "connect_failures",
        "reconnects",
        "dao_total_calls",
        "dao_total_failures",
        "user_dao",
        "note_dao",
        "message_dao",
    ):
        assert field in body
    for dao_field in ("user_dao", "note_dao", "message_dao"):
        dao_body = body[dao_field]
        for field in ("class", "module", "collection", "calls",
                      "total_calls", "total_failures"):
            assert field in dao_body


def test_status_html_renders_table(client):
    response = client.get("/status")
    assert response.status_code == 200
    assert b"Persistence status" in response.data
    assert b"connection_mode" in response.data


def test_status_counts_observed_dao_calls(client, stores):
    stores["users"].append(_user())
    _login(client)
    body = client.get("/status?format=json").get_json()
    assert body["dao_total_calls"] >= 1
    assert body["user_dao"]["total_calls"] >= 1
    assert "FakeUserDao.find_one" in body["user_dao"]["calls"]
