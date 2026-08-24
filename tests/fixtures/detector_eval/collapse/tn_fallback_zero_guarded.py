def read_latency(path, raw):
    if not path.exists():
        return 0
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError("invalid latency") from error
