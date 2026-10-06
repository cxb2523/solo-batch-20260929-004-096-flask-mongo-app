"""DAO for the ``messages`` collection."""

from persistence.base import BaseDao


class MessageDao(BaseDao):
    collection_name = "messages"

    def inbox(self, user_id):
        cursor = self.run(
            "find", {"to_id": user_id, "deleted": False}
        )
        return list(cursor.sort("timestamp", -1))

    def sent(self, user_id):
        cursor = self.run(
            "find",
            {
                "from_id": user_id,
                "deleted": False,
                "hidden_for_sender": False,
            },
        )
        return list(cursor.sort("timestamp", -1))

    def insert(self, message_data):
        return self.run("insert_one", message_data)

    def soft_delete(self, message_id):
        return self.run(
            "update_one", {"id": message_id}, {"$set": {"deleted": True}}
        )

    def hide_from_sender(self, message_id):
        return self.run(
            "update_one",
            {"id": message_id},
            {"$set": {"hidden_for_sender": True}},
        )

    def delete_for_user(self, user_id):
        return self.run(
            "delete_many",
            {"$or": [{"from_id": user_id}, {"to_id": user_id}]},
        )
