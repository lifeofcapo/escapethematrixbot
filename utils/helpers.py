import secrets
import string
from datetime import datetime, timezone

def generate_profile_key(length: int = 24) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def generate_sub_email(user_id: int) -> str:
    rand = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"fi{rand}"


def format_datetime(dt_val, lang: str = "ru") -> str:
    """Format datetime (string or datetime object) to human-readable."""
    try:
        if isinstance(dt_val, str):
            dt = datetime.fromisoformat(dt_val)
        else:
            dt = dt_val
        # Normalize to UTC naive for display
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        if lang == "ru":
            return dt.strftime("%d.%m.%Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(dt_val)


def days_left(dt_val) -> int:
    try:
        if isinstance(dt_val, str):
            expires = datetime.fromisoformat(dt_val)
        else:
            expires = dt_val
        if expires.tzinfo is not None:
            expires = expires.astimezone(timezone.utc).replace(tzinfo=None)
        delta = (expires - datetime.now(timezone.utc)).days
        return max(delta, 0)
    except Exception:
        return 0