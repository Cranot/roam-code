import json


def load_options(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
