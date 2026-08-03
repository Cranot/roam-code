"""POSITIVE CONTROL for ``scripts/scan_snake_case_violations.py`` — DO NOT "FIX".

A planted camelCase function definition, never imported and never executed.
The scanner must report EXACTLY one finding in this file:

    function  sentinelCamelCase

``sentinel_snake_case`` and ``__sentinel_dunder__`` are the negative half:
they must NOT be reported, so a detector rewired to flag every function
cannot satisfy this control by firing indiscriminately.

If the camelCase name stops being reported, the scanner is BROKEN and its
"matches the baseline" verdict against ``src/roam`` means nothing.
"""


def sentinelCamelCase():  # noqa: N802 - planted defect, see module docstring
    return None


def sentinel_snake_case():
    return None


def __sentinel_dunder__():
    return None
