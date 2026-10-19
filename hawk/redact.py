"""Guard against a snapshot ever carrying a credential into git history."""

FORBIDDEN = ("PASSWORD", "SECRET", "_KEY", "TOKEN", "CREDENTIAL")


class SecretLeak(Exception):
    pass


def _offending_marker(text):
    upper = text.upper()
    for marker in FORBIDDEN:
        if marker in upper:
            return marker
    return None


def _walk(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            marker = _offending_marker(str(key))
            if marker:
                raise SecretLeak(f"key at {path}.{key} looks like a secret ({marker})")
            _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]")
    elif isinstance(node, str):
        marker = _offending_marker(node)
        if marker:
            raise SecretLeak(f"value at {path} looks like a secret ({marker})")


def assert_no_secrets(snapshot):
    """Raise SecretLeak if the snapshot contains anything credential-shaped."""
    _walk(snapshot, "$")
