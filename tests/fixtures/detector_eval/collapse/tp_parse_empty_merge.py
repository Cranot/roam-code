import json


def decode_options(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}
