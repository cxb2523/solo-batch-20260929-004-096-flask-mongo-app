"""DAO for the ``users`` collection."""

from persistence.base import BaseDao


class UserDao(BaseDao):
    collection_name = "users"

    def find_by_email(self, email, include_id_field=True):
        projection = None if include_id_field else {"_id": 0}
        return self.run("find_one", {"email": email}, projection)

    def find_by_id(self, user_id):
        return self.run("find_one", {"id": user_id}, {"_id": 0})

    def find_all_except(self, user_id):
        return list(
            self.run("find", {"id": {"$ne": user_id}}, {"_id": 0})
        )

    def insert(self, user_data):
        return self.run("insert_one", user_data)

    def mark_verified(self, email):
        return self.run(
            "update_one", {"email": email}, {"$set": {"verified": True}}
        )

    def update_name(self, email, title, first_name, last_name):
        return self.run(
            "update_one",
            {"email": email},
            {
                "$set": {
                    "title": title,
                    "first_name": first_name,
                    "last_name": last_name,
                }
            },
        )

    def delete_by_id(self, user_id):
        return self.run("delete_one", {"id": user_id})
