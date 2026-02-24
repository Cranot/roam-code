"""Tests for URL utilities."""

from __future__ import annotations

import pytest

from webcrawler.url_utils import (
    get_domain,
    is_external_link,
    is_same_domain,
    is_valid_url,
    normalize_url,
    resolve_url,
)


class TestNormalizeUrl:
    """Tests for URL normalization."""

    def test_normalize_removes_fragment(self):
        url = "https://example.com/page#section"
        assert normalize_url(url) == "https://example.com/page"

    def test_normalize_preserves_query(self):
        url = "https://example.com/page?q=test"
        assert normalize_url(url) == "https://example.com/page?q=test"

    def test_normalize_adds_trailing_slash_to_root(self):
        url = "https://example.com"
        assert normalize_url(url) == "https://example.com/"

    def test_normalize_preserves_path(self):
        url = "https://example.com/path/to/page"
        assert normalize_url(url) == "https://example.com/path/to/page"


class TestIsValidUrl:
    """Tests for URL validation."""

    def test_valid_http_url(self):
        assert is_valid_url("http://example.com") is True

    def test_valid_https_url(self):
        assert is_valid_url("https://example.com") is True

    def test_invalid_scheme(self):
        assert is_valid_url("ftp://example.com") is False

    def test_no_scheme(self):
        assert is_valid_url("example.com") is False

    def test_no_domain(self):
        assert is_valid_url("https://") is False

    def test_malformed_url(self):
        assert is_valid_url("not a url") is False


class TestGetDomain:
    """Tests for domain extraction."""

    def test_get_domain_basic(self):
        assert get_domain("https://example.com/page") == "example.com"

    def test_get_domain_with_subdomain(self):
        assert get_domain("https://www.example.com/page") == "www.example.com"

    def test_get_domain_with_port(self):
        assert get_domain("https://example.com:8080/page") == "example.com:8080"

    def test_get_domain_invalid_url(self):
        assert get_domain("not a url") is None


class TestIsSameDomain:
    """Tests for same domain checking."""

    def test_same_domain(self):
        url1 = "https://example.com/page1"
        url2 = "https://example.com/page2"
        assert is_same_domain(url1, url2) is True

    def test_different_domains(self):
        url1 = "https://example.com/page"
        url2 = "https://other.com/page"
        assert is_same_domain(url1, url2) is False

    def test_subdomain_difference(self):
        url1 = "https://www.example.com/page"
        url2 = "https://api.example.com/page"
        assert is_same_domain(url1, url2) is False


class TestResolveUrl:
    """Tests for URL resolution."""

    def test_resolve_absolute_url(self):
        base = "https://example.com/page"
        url = "https://other.com/page"
        assert resolve_url(base, url) == "https://other.com/page"

    def test_resolve_relative_path(self):
        base = "https://example.com/path/page"
        url = "../other"
        assert resolve_url(base, url) == "https://example.com/other"

    def test_resolve_root_relative(self):
        base = "https://example.com/path/page"
        url = "/other"
        assert resolve_url(base, url) == "https://example.com/other"

    def test_resolve_current_dir_relative(self):
        base = "https://example.com/path/page"
        url = "other"
        assert resolve_url(base, url) == "https://example.com/path/other"


class TestIsExternalLink:
    """Tests for external link detection."""

    def test_internal_link(self):
        base = "https://example.com/page"
        link = "https://example.com/other"
        assert is_external_link(base, link) is False

    def test_external_link(self):
        base = "https://example.com/page"
        link = "https://other.com/page"
        assert is_external_link(base, link) is True

    def test_subdomain_is_external(self):
        base = "https://example.com/page"
        link = "https://www.example.com/page"
        assert is_external_link(base, link) is True
