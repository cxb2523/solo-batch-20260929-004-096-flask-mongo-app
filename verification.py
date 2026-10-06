from itsdangerous import URLSafeTimedSerializer

from persistence.config import load_settings

# Configuration is now resolved by the persistence layer's config reader,
# removing the duplicate ConfigParser instantiation.
_settings = load_settings()
SECRET_KEY = _settings.secret_key
SECURITY_PASSWORD_SALT = _settings.security_password_salt

def generate_confirmation_token(email):
    serializer = URLSafeTimedSerializer(SECRET_KEY)
    return serializer.dumps(email, salt=SECURITY_PASSWORD_SALT)

def confirm_token(token, expiration=3600):
    serializer = URLSafeTimedSerializer(SECRET_KEY)
    try:
        email = serializer.loads(
            token,
            salt=SECURITY_PASSWORD_SALT,
            max_age=expiration
        )
    except:
        return False
    return email
