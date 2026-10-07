import threading

from pymongo.errors import DuplicateKeyError, PyMongoError

from .connection import get_factory
from .exceptions import DuplicateEmailError, PersistenceError


class BaseDAO:
    # Base class for entity DAOs.
    #
    # Every public operation goes through _run(), which:
    # - bumps a thread-safe call counter (for the /status page)
    # - translates pymongo failures into PersistenceError
    # - resets the cached connection on failure so the next call
    #   reconnects without restarting the process

    collection_name = None

    def __init__(self, factory=None):
        self._factory = factory or get_factory()
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def call_count(self):
        with self._lock:
            return self._calls

    def _collection(self):
        return self._factory.get_db()[self.collection_name]

    def _run(self, operation):
        # operation: callable taking the Mongo collection
        with self._lock:
            self._calls += 1
        try:
            return operation(self._collection())
        except DuplicateKeyError:
            raise
        except PyMongoError as exc:
            self._factory.reset()
            raise PersistenceError(str(exc)) from exc


class UserDAO(BaseDAO):
    collection_name = "users"

    def find_by_email(self, email):
        return self._run(lambda c: c.find_one({"email": email}, {"_id": 0}))

    def find_by_id(self, user_id):
        return self._run(lambda c: c.find_one({"id": user_id}, {"_id": 0}))

    def find_all_except(self, user_id):
        return self._run(lambda c: c.find({"id": {"$ne": user_id}}, {"_id": 0}))

    def insert(self, user_data):
        try:
            return self._run(lambda c: c.insert_one(user_data))
        except DuplicateKeyError as exc:
            raise DuplicateEmailError(
                "email already registered: %s" % user_data.get("email")) from exc

    def mark_verified(self, email):
        return self._run(lambda c: c.update_one(
            {"email": email}, {"$set": {"verified": True}}))

    def update_name(self, email, title, first_name, last_name):
        return self._run(lambda c: c.update_one(
            {"email": email},
            {"$set": {"title": title, "first_name": first_name,
                      "last_name": last_name}}))

    def delete_by_id(self, user_id):
        return self._run(lambda c: c.delete_one({"id": user_id}))


class NoteDAO(BaseDAO):
    collection_name = "notes"

    def find_for_user(self, user_id):
        return self._run(lambda c: c.find(
            {"user_id": user_id, "deleted": False}).sort("timestamp", -1))

    def insert(self, note_data):
        return self._run(lambda c: c.insert_one(note_data))

    def mark_deleted(self, note_id):
        return self._run(lambda c: c.update_one(
            {"id": note_id}, {"$set": {"deleted": True}}))

    def delete_for_user(self, user_id):
        return self._run(lambda c: c.delete_many({"user_id": user_id}))


class MessageDAO(BaseDAO):
    collection_name = "messages"

    def find_inbox(self, user_id):
        return self._run(lambda c: c.find(
            {"to_id": user_id, "deleted": False}).sort("timestamp", -1))

    def find_sent(self, user_id):
        return self._run(lambda c: c.find(
            {"from_id": user_id, "deleted": False,
             "hidden_for_sender": False}).sort("timestamp", -1))

    def insert(self, message_data):
        return self._run(lambda c: c.insert_one(message_data))

    def mark_deleted(self, message_id):
        return self._run(lambda c: c.update_one(
            {"id": message_id}, {"$set": {"deleted": True}}))

    def mark_hidden_for_sender(self, message_id):
        return self._run(lambda c: c.update_one(
            {"id": message_id}, {"$set": {"hidden_for_sender": True}}))

    def delete_for_user(self, user_id):
        return self._run(lambda c: c.delete_many(
            {"$or": [{"from_id": user_id}, {"to_id": user_id}]}))
