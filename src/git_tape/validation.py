from __future__ import annotations


def require_int(value: object, name: str) -> int:
    """Accept integer settings without truncating floats or accepting booleans."""
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value
