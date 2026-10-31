"""Config helpers with no third-party imports, shared by the batch and the API."""


def deep_merge(base: dict, overlay: dict) -> dict:
    """`base` with `overlay` on top; nested dicts merge key by key."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
