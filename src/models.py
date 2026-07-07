class User:
    """A user model."""
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def display_name(self):
        return self.name.title()

    def validate_email(self):
        return "@" in self.email


class Admin(User):
    """An admin user."""
    def __init__(self, name, email, role="admin"):
        super().__init__(name, email)
        self.role = role

    def promote(self, user):
        pass
