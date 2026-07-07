"""F12 regression — default role exclusions for docs_src/ and friends.

D1b stranger battery: fastapi shipped every tutorial as a deliberately
standalone, untested teaching snippet under ``docs_src/``. Those 375
coverage-gap "violations" + 7/8 top dead-SAFE findings were ALL ``docs_src/``.
The plain ``docs`` segment match missed ``docs_src`` (``docs_src != docs``).
This locks the added default plus the config override plumbing.
"""

from __future__ import annotations

from roam.output.file_role_hints import (
    _split_dir_list,
    is_excluded_path,
)


def test_docs_src_now_excluded() -> None:
    # The exact fastapi false-positive path.
    assert is_excluded_path("docs_src/additional_responses/tutorial001_py310.py")
    assert is_excluded_path("docs_src/advanced_middleware/tutorial001_py310.py")
    # spelling variants of the same convention
    assert is_excluded_path("doc_src/x.py")
    assert is_excluded_path("docs_source/y.py")


def test_existing_defaults_unchanged() -> None:
    assert is_excluded_path("examples/mvc/controllers/user/index.js")
    assert is_excluded_path("docs/en/tutorial.md")
    assert is_excluded_path("build/generated.py")
    # a real source path is NOT excluded
    assert not is_excluded_path("fastapi/routing.py")
    assert not is_excluded_path("src/requests/adapters.py")
    assert not is_excluded_path(None)


def test_config_override_add_and_reinclude() -> None:
    # extra_dirs adds a project-specific exclusion...
    assert is_excluded_path("proto/service_pb2.py", extra_dirs=frozenset({"proto"}))
    # ...and allow_dirs re-admits a default (a project that ships real source
    # under examples/ opts back in).
    assert not is_excluded_path("examples/app.py", allow_dirs=frozenset({"examples"}))


def test_split_dir_list_parser() -> None:
    assert _split_dir_list("a, b ,c") == frozenset({"a", "b", "c"})
    assert _split_dir_list("") == frozenset()
    assert _split_dir_list(None) == frozenset()
    assert _split_dir_list(123) == frozenset()
