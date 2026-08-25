def recursive_target(remaining):
    if remaining <= 0:
        return 0
    return recursive_target(remaining - 1)
