from models import User, Admin


def create_user(name, email):
    """Create a new user."""
    user = User(name, email)
    if not user.validate_email():
        raise ValueError("Invalid email")
    return user


def get_display(user):
    """Get display name."""
    return user.display_name()


def unused_helper():
    """This function is never called (dead code)."""
    return 42
