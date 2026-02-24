"""URL handling and validation utilities."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse


def normalize_url(url: str) -> str:
    """
    Normalize a URL by removing fragments and standardizing format.

    Args:
        url: URL to normalize

    Returns:
        Normalized URL string
    """
    parsed = urlparse(url)
    # Remove fragment and reconstruct
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path or '/',
        parsed.params,
        parsed.query,
        ''  # Remove fragment
    ))
    return normalized


def is_valid_url(url: str) -> bool:
    """
    Check if a URL is valid and uses http/https scheme.

    Args:
        url: URL to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        parsed = urlparse(url)
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False


def get_domain(url: str) -> Optional[str]:
    """
    Extract domain from URL.

    Args:
        url: URL to extract domain from

    Returns:
        Domain string or None if invalid
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return parsed.netloc
    except Exception:
        return None


def is_same_domain(url1: str, url2: str) -> bool:
    """
    Check if two URLs belong to the same domain.

    Args:
        url1: First URL
        url2: Second URL

    Returns:
        True if same domain, False otherwise
    """
    return get_domain(url1) == get_domain(url2)


def resolve_url(base_url: str, url: str) -> str:
    """
    Resolve a potentially relative URL against a base URL.

    Args:
        base_url: Base URL for resolution
        url: URL to resolve (may be relative)

    Returns:
        Absolute URL
    """
    return urljoin(base_url, url)


def is_external_link(base_url: str, link_url: str) -> bool:
    """
    Check if a link is external to the base domain.

    Args:
        base_url: Base URL to compare against
        link_url: Link URL to check

    Returns:
        True if external, False if internal
    """
    return not is_same_domain(base_url, link_url)
