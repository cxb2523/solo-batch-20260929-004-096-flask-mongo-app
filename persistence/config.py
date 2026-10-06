"""Centralised configuration reading.

Previously ``run.py`` and ``verification.py`` each built their own
``configparser`` and duplicated env-var handling.
"""

import configparser
import os


CONFIG_PATH = os.environ.get("CONFIG_PATH", "configuration.ini")


class Settings:
    """Resolved application settings."""

    def __init__(
        self,
        secret_key,
        security_password_salt,
        mongo_uri,
        mongo_dbname,
        server_timeout_ms=5000,
        connect_timeout_ms=3000,
    ):
        self.secret_key = secret_key
        self.security_password_salt = security_password_salt
        self.mongo_uri = mongo_uri
        self.mongo_dbname = mongo_dbname
        self.server_timeout_ms = server_timeout_ms
        self.connect_timeout_ms = connect_timeout_ms


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_settings(config_path=CONFIG_PATH):
    """Read the ini file once and let environment variables override it."""
    parser = configparser.ConfigParser()
    parser.read(config_path)
    default = parser["DEFAULT"] if parser.has_section("DEFAULT") else {}

    secret_key = os.environ.get("SECRET_KEY")
    if secret_key is None:
        secret_key = default.get("SECRET_KEY", "Change this before production")

    salt = os.environ.get("SECURITY_PASSWORD_SALT")
    if salt is None:
        salt = default.get("SECURITY_PASSWORD_SALT", "Security password salt")

    mongo_uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")
    if mongo_uri is None:
        mongo_uri = default.get("MONGO_URI", "")

    mongo_dbname = os.environ.get("MONGO_DBNAME") or os.environ.get("DATABASE_NAME")
    if mongo_dbname is None:
        mongo_dbname = default.get("DATABASE_NAME", "sample")

    return Settings(
        secret_key=secret_key,
        security_password_salt=salt,
        mongo_uri=mongo_uri,
        mongo_dbname=mongo_dbname,
        server_timeout_ms=_env_int("MONGO_SERVER_TIMEOUT_MS", 5000),
        connect_timeout_ms=_env_int("MONGO_CONNECT_TIMEOUT_MS", 3000),
    )
