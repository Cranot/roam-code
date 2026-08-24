import json


def decode_options(raw):
    if not raw:
        return {"state": "empty", "value": {}}
    try:
        return {"state": "present", "value": json.loads(raw)}
    except (TypeError, ValueError) as error:
        return {"state": "invalid", "reason": str(error)}
