def function_a():
    return 42


def function_b():
    return function_a()


def main():
    return function_b()
