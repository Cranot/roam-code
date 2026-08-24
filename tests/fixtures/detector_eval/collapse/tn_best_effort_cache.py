def load_cache(store):
    # Best-effort cache: a miss is allowed to trigger recomputation.
    try:
        return store.read()
    except OSError:
        return {}
