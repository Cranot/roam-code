"""Wildcard backend HTTP methods must reach endpoint matching."""

import pytest

from roam.workspace.api_scanner import find_unmatched_calls, match_api_endpoints


def _endpoint(path, method, file_path):
    return {
        "url_pattern": path,
        "http_method": method,
        "file_path": file_path,
        "line": 1,
        "symbol_id": 1,
        "symbol_name": "handler",
    }


@pytest.mark.parametrize("backend_method", ["ANY", "RESOURCE", "any", "resource"])
@pytest.mark.parametrize("call_method", ["GET", "POST"])
def test_wildcard_backend_method_matches_concrete_call(backend_method, call_method):
    call = _endpoint("/api/users", call_method, "client.js")
    route = _endpoint("/users", backend_method, "server.js")

    matches = match_api_endpoints([call], [route])

    assert len(matches) == 1
    assert matches[0]["frontend"] == call
    assert matches[0]["backend"] == route
    assert find_unmatched_calls([call], [route], matches) == []


@pytest.mark.parametrize("backend_method", ["POST", "", "GET"])
def test_concrete_and_unspecified_backend_methods(backend_method):
    call = _endpoint("/api/users", "POST", "client.js")
    route = _endpoint("/users", backend_method, "server.js")

    matches = match_api_endpoints([call], [route])

    if backend_method == "GET":
        assert matches == []
        unmatched = find_unmatched_calls([call], [route], matches)
        assert len(unmatched) == 1
        assert unmatched[0]["reason"] == "method_mismatch"
    else:
        assert len(matches) == 1
        assert matches[0]["backend"] == route


def test_wildcard_method_does_not_match_unrelated_path():
    call = _endpoint("/api/users", "GET", "client.js")
    route = _endpoint("/products", "ANY", "server.js")

    assert match_api_endpoints([call], [route]) == []
