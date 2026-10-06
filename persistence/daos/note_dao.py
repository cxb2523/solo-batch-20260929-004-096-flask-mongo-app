"""DAO for the ``notes`` collection."""

from persistence.base import BaseDao


class NoteDao(BaseDao):
    collection_name = "notes"

    def active_for_user(self, user_id):
        cursor = self.run(
            "find", {"user_id": user_id, "deleted": False}
        )
        return list(cursor.sort("timestamp", -1))

    def insert(self, note_data):
        return self.run("insert_one", note_data)

    def soft_delete(self, note_id):
        return self.run(
            "update_one", {"id": note_id}, {"$set": {"deleted": True}}
        )

    def delete_for_user(self, user_id):
        return self.run("delete_many", {"user_id": user_id})
