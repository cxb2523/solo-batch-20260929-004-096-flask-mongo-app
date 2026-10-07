class PersistenceError(Exception):
    # Raised when a database operation fails (connection, timeout, etc.)
    pass


class DuplicateEmailError(PersistenceError):
    # Raised when inserting a user whose email already exists
    pass
