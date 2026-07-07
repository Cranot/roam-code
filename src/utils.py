def format_name(first, last):
    """Format a full name."""
    return f"{first} {last}"


def parse_email(raw):
    """Parse an email address."""
    if "@" not in raw:
        return None
    parts = raw.split("@")
    return {"user": parts[0], "domain": parts[1]}


UNUSED_CONSTANT = "never_referenced"
