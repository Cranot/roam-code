"""No database, no I/O — pure in-memory dict/set/list operations.

`effects` classifies these as reads_db / writes_db because its regex matches
`.get(` / `.add(` / `.update(` on ANY receiver. That is a false positive:
none of these functions touch a database.
"""


def lookup_settings(config):
    host = config.get("host")   # dict.get -> effects flags reads_db (FP)
    port = config.get("port")
    return host, port


def collect(items):
    seen = set()
    for it in items:
        seen.add(it)            # set.add -> effects flags writes_db (FP)
    return seen


def merge(a, b):
    a.update(b)                 # dict.update -> effects flags writes_db (FP)
    return a
