"""W1438 — the ``roam`` these tests import must be the ``roam`` in this checkout.

Why this regression test exists
--------------------------------

A sibling service had a severe defect of exactly one shape: production ran a
compiled artefact while every verification ran against source. A fix was
verified green and sat unbuilt for hours while production kept executing the
pre-fix code. Nothing failed. Nothing looked wrong. The whole class is
"something is verified in one form and executed in another", and it is
invisible precisely because both halves are individually healthy.

This repository is src-layout, which makes it eligible for the Python version
of that defect. ``import roam`` resolves through ``sys.path``, so it lands on
whatever install wins — an editable ``.pth`` pointing at ``src/``, or a
previously-copied build sitting in ``site-packages``/``dist-packages``. A
copied install does not track the checkout, and nothing announces the
difference.

Measured, not hypothesised. On the deployment host, ``python3 -m pytest`` from
inside the checkout registered plugins out of
``/usr/local/lib/python3.12/dist-packages/roam/`` — a copied install pinned to a
commit 122 behind the working tree, differing in 164 files, and missing a
shipped taint fix. The test FILES came from the checkout; the LIBRARY they
exercised did not. Every result was a true statement about code nobody was
editing.

What this pins
--------------

If a ``src/roam`` exists beside these tests, the imported package must be that
one. That is the whole property, and it is exact: no version strings are
involved, because ``roam.__version__`` reads ``importlib.metadata`` and reports
the INSTALLED distribution's number — an editable install whose metadata was
written at 13.8.0 keeps saying 13.8.0 while correctly importing 13.10.0 source.
A version comparison here would be a coin flip; a path comparison cannot be.

The guard is silent where it would be wrong: the wheel lanes install the
package into a throwaway environment and run from outside the repository, on
purpose, so ``src/`` is legitimately absent there and this test skips itself
rather than fighting the thing it is meant to protect.
"""

from __future__ import annotations

import pathlib

import pytest

import roam
from tests._helpers.repo_root import repo_root


def _checkout_package() -> pathlib.Path:
    return (repo_root() / "src" / "roam").resolve()


def test_imported_roam_is_this_checkout() -> None:
    expected = _checkout_package()
    if not expected.is_dir():
        pytest.skip(
            "no src/roam beside this test — running from an installed artefact "
            "(wheel/sdist lane), where there is no checkout to shadow"
        )

    imported = pathlib.Path(roam.__file__).resolve().parent

    assert imported == expected, (
        "the tests are exercising a DIFFERENT copy of roam than this checkout.\n"
        f"  imported: {imported}\n"
        f"  checkout: {expected}\n"
        "Whatever this run proves, it proves about the copy above, not about the "
        "code in this working tree — a green suite here says nothing about the "
        "change you just made.\n"
        "Run the suite through this checkout's environment instead, e.g. "
        "`uv run pytest`, or `.venv/bin/python -m pytest` "
        "(`.venv\\Scripts\\python.exe -m pytest` on Windows)."
    )
