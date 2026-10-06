# MODULE IMPORTS

# Flask modules
from flask import (
    Flask,
    render_template,
    request,
    url_for,
    redirect,
    abort,
    jsonify,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_talisman import Talisman
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect

# Other modules
from urllib.parse import urlparse, urljoin
import json
import os

# Local imports
from user import User, Anonymous
from message import Message
from note import Note
from email_utility import (
    send_registration_email,
    send_message_email,
)
from verification import confirm_token

# Persistence layer (routes talk ONLY to these DAOs; no pymongo here)
from persistence import get_registry, get_connection_provider, load_settings
from persistence.errors import ConnectionUnavailable, DaoTimeout


def create_app(settings=None, provider=None):
    app = Flask(__name__)

    # Configuration -- read once through the persistence layer's config reader
    settings = settings if settings is not None else load_settings()
    app.secret_key = settings.secret_key
    app.config["MONGO_DBNAME"] = settings.mongo_dbname
    app.config["MONGO_URI"] = settings.mongo_uri
    app.config["PREFERRED_URL_SCHEME"] = "https"

    # Wire the Mongo singleton into the DAO registry.  Production reuses the
    # process-wide provider; tests/harnesses inject their own factory-backed
    # provider (e.g. mongomock) without touching pymongo.
    if provider is None:
        provider = get_connection_provider(settings=settings)
    registry = get_registry()
    registry.bind_provider(provider)
    app.extensions["dao_registry"] = registry
    app.extensions["connection_provider"] = provider

    # Create Bcrypt
    bc = Bcrypt(app)

    # Create Talisman (the test harness disables forced HTTPS + CSP)
    force_https = os.environ.get("APP_FORCE_HTTPS", "1") != "0"
    csp = {
        "default-src": [
            "'self'",
            "https://stackpath.bootstrapcdn.com",
            "https://pro.fontawesome.com",
            "https://code.jquery.com",
            "https://cdnjs.cloudflare.com",
        ]
    }
    talisman = Talisman(
        app,
        content_security_policy=csp if force_https else None,
        force_https=force_https,
    )

    # Create CSRF protect (disabled in the test harness, see below)
    csrf = CSRFProtect()
    csrf.init_app(app)
    if os.environ.get("APP_CSRF_ENABLED", "1") == "0":
        app.config["WTF_CSRF_ENABLED"] = False

    # Create login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.anonymous_user = Anonymous
    login_manager.login_view = "login"

    def user_dao():
        return registry.get_user_dao()

    def note_dao():
        return registry.get_note_dao()

    def message_dao():
        return registry.get_message_dao()

    # Infrastructure failures only: DB down / timeout -> 503.
    # Business outcomes (duplicate email, unverified user, empty results)
    # keep their original per-route status codes below.
    @app.errorhandler(ConnectionUnavailable)
    @app.errorhandler(DaoTimeout)
    def handle_dao_infrastructure_error(error):
        payload = {
            "error": "database_unavailable",
            "error_type": type(error).__name__,
        }
        if request.path.startswith("/api") or request.is_json:
            return jsonify(payload), 503
        return render_template("confirm.html", success=False), 503

    # ROUTES

    # Index
    @app.route("/")
    def index():
        return render_template("index.html")

    # Status -- connection mode and per-DAO call counters (for screen checks)
    @app.route("/status")
    def status():
        snapshot = registry.status_snapshot()
        active_provider = app.extensions["connection_provider"]
        # Ping first so the returned stats already reflect that connection.
        ping_ok = active_provider.ping()
        connection_stats = active_provider.stats()
        body = {
            "connection_mode": snapshot["connection_mode"],
            "mongo_client_connected": connection_stats["client_connected"],
            "mongo_ping_ok": ping_ok,
            "mongo_db": connection_stats["dbname"],
            "connect_attempts": connection_stats["connect_attempts"],
            "connect_failures": connection_stats["connect_failures"],
            "reconnects": connection_stats["reconnects"],
            "dao_total_calls": snapshot["dao_total_calls"],
            "dao_total_failures": snapshot["dao_total_failures"],
            "user_dao": snapshot["daos"]["user"],
            "note_dao": snapshot["daos"]["note"],
            "message_dao": snapshot["daos"]["message"],
        }
        if request.args.get("format") == "json":
            return jsonify(body), 200
        return render_template("status.html", status=body, raw=json.dumps(
            body, indent=2, sort_keys=True, default=str
        ))

    # Login
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if current_user.is_authenticated:
                # Redirect to index if already authenticated
                return redirect(url_for("/index"))
            # Render login page
            return render_template(
                "login.html", error=request.args.get("error")
            )
        # Retrieve user from database via DAO
        user_data = user_dao().find_by_email(request.form["email"])
        if user_data:
            # Check password hash
            if bc.check_password_hash(
                user_data["password"], request.form["pass"]
            ):
                # Create user object to login
                # (note password hash not stored in session)
                user = User.make_from_dict(user_data)
                login_user(user)

                # Check for next argument (direct user to protected page)
                next_url = request.args.get("next")
                if not is_safe_url(next_url):
                    return abort(400)

                # Go to profile page after login
                return redirect(next_url or url_for("profile"))

        # Redirect to login page on error
        return redirect(url_for("login", error=1))

    # Register
    @app.route("/register", methods=["POST", "GET"])
    def register():
        if request.method == "POST":
            # Trim input data
            email = request.form["email"].strip()
            title = request.form["title"].strip()
            first_name = request.form["first_name"].strip()
            last_name = request.form["last_name"].strip()
            password = request.form["pass"].strip()

            # Check if email address already exists
            existing_user = user_dao().find_by_email(email)

            if existing_user is None:
                logout_user()
                # Hash password
                hashpass = bc.generate_password_hash(password).decode("utf-8")
                # Create user object
                # (note password hash not stored in session)
                new_user = User(title, first_name, last_name, email)
                # Create dictionary data to save to database
                user_data_to_save = new_user.dict()
                user_data_to_save["password"] = hashpass

                # Insert user record to database
                if user_dao().insert(user_data_to_save):
                    login_user(new_user)
                    send_registration_email(new_user)
                    return redirect(url_for("profile"))
                else:
                    # Handle database error
                    return redirect(url_for("register", error=2))

            # Handle duplicate email (business outcome, not a 500)
            return redirect(url_for("register", error=1))

        # Return template for registration page if GET request
        return render_template(
            "register.html", error=request.args.get("error")
        )

    # Confirm email
    @app.route("/confirm/<token>", methods=["GET"])
    def confirm_email(token):
        logout_user()
        try:
            email = confirm_token(token)
            if email:
                if user_dao().mark_verified(email):
                    return render_template("confirm.html", success=True)
        except ConnectionUnavailable:
            raise
        except DaoTimeout:
            raise
        except Exception:
            return render_template("confirm.html", success=False)
        else:
            return render_template("confirm.html", success=False)

    # Verification email
    @app.route("/verify", methods=["POST"])
    @login_required
    def send_verification_email():
        if current_user.verified is False:
            send_registration_email(current_user)
            return "Verification email sent"
        else:
            return "Your email address is already verified"

    # Profile
    @app.route("/profile", methods=["GET"])
    @login_required
    def profile():
        notes = note_dao().active_for_user(current_user.id)
        return render_template(
            "profile.html", notes=notes, title=current_user.title
        )

    # Messages
    @app.route("/messages", methods=["GET"])
    @login_required
    def messages():
        all_users = user_dao().find_all_except(current_user.id)
        inbox_messages = message_dao().inbox(current_user.id)
        sent_messages = message_dao().sent(current_user.id)
        return render_template(
            "messages.html",
            users=all_users,
            inbox_messages=inbox_messages,
            sent_messages=sent_messages,
        )

    # Logout
    @app.route("/logout", methods=["GET"])
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    # POST REQUEST ROUTES

    # Add note
    @app.route("/add_note", methods=["POST"])
    @login_required
    def add_note():
        title = request.form.get("title")
        body = request.form.get("body")
        user_id = current_user.id
        user_name = current_user.display_name()
        note = Note(title, body, user_id, user_name)
        if note_dao().insert(note.dict()):
            return "Success! Note added: " + title
        else:
            return "Error! Could not add note"

    # Delete note
    @app.route("/delete_note", methods=["POST"])
    @login_required
    def delete_note():
        note_id = request.form.get("note_id")
        if note_dao().soft_delete(note_id):
            return "Success! Note deleted"
        else:
            return "Error! Could not delete note"

    # Send message
    @app.route("/send_message", methods=["POST"])
    @login_required
    def send_message():
        title = request.form.get("title")
        body = request.form.get("body")
        from_id = current_user.id
        from_name = current_user.display_name()
        to_id = request.form.get("user")
        to_user_dict = user_dao().find_by_id(to_id)
        if to_user_dict is None:
            # Empty recipient result: 404, not a 500 crash
            # (the original code raised TypeError here).
            return "Error! Recipient user not found", 404
        to_user = User.make_from_dict(to_user_dict)
        to_name = to_user.display_name()
        message = Message(
            title, body, from_id, from_name, to_id, to_name
        )
        if message_dao().insert(message.dict()):
            send_message_email(
                from_user=current_user, to_user=to_user, message=message
            )
            return "Success! Message sent to " + to_name + ": " + title
        else:
            return "Error! Could not send message"

    # Delete message
    @app.route("/delete_message", methods=["POST"])
    @login_required
    def delete_message():
        message_id = request.form.get("message_id")
        if message_dao().soft_delete(message_id):
            return "Success! Message deleted"
        else:
            return "Error! Could not delete message"

    # Hide sent message
    @app.route("/hide_sent_message", methods=["POST"])
    @login_required
    def hide_sent_message():
        message_id = request.form.get("message_id")
        if message_dao().hide_from_sender(message_id):
            return "Success! Message hidden from sender"
        else:
            return "Error! Could not hide message"

    # Change Name
    @app.route("/change_name", methods=["POST"])
    @login_required
    def change_name():
        title = request.form["title"].strip()
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()

        if user_dao().update_name(
            current_user.email, title, first_name, last_name
        ):
            return "User name updated successfully"
        else:
            return "Error! Could not update user name"

    # Delete Account
    @app.route("/delete_account", methods=["POST"])
    @login_required
    def delete_account():
        user_id = current_user.id

        # Deletion flags
        user_deleted = False
        notes_deleted = False
        messages_deleted = False

        # Delete user details
        if user_dao().delete_by_id(user_id):
            user_deleted = True
            logout_user()

        # Delete notes
        if note_dao().delete_for_user(user_id):
            notes_deleted = True

        # Delete messages
        if message_dao().delete_for_user(user_id):
            messages_deleted = True

        return {
            "user_deleted": user_deleted,
            "notes_deleted": notes_deleted,
            "messages_deleted": messages_deleted,
        }

    # LOGIN MANAGER REQUIREMENTS

    # Load user from user ID
    @login_manager.user_loader
    def load_user(userid):
        # Return user object or none
        user = user_dao().find_by_id(userid)
        if user:
            return User.make_from_dict(user)
        return None

    # Safe URL
    def is_safe_url(target):
        ref_url = urlparse(request.host_url)
        test_url = urlparse(urljoin(request.host_url, target))
        return test_url.scheme in ("http", "https") and ref_url.netloc == (
            test_url.netloc
        )

    return app


app = create_app()


# Entry point: python run.py
if __name__ == "__main__":
    if os.environ.get("APP_LOCATION") == "heroku":
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
    else:
        port = int(os.environ.get("PORT", 8080))
        app.run(host="localhost", port=port, debug=True, use_reloader=False)
