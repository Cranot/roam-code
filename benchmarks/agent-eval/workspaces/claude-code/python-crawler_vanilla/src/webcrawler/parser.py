"""HTML parser — extracts structured data from pages."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def parse_page(
    html: str, base_url: str, base_domain: str
) -> Dict[str, object]:
    """Parse an HTML page and extract structured data.

    Returns a dict with keys: title, meta_description, headings,
    internal_links, external_links, images.
    """
    soup = BeautifulSoup(html, "html.parser")

    title = _extract_title(soup)
    meta_description = _extract_meta_description(soup)
    headings = _extract_headings(soup)
    internal_links, external_links = _extract_links(soup, base_url, base_domain)
    images = _extract_images(soup, base_url)

    return {
        "title": title,
        "meta_description": meta_description,
        "headings": headings,
        "internal_links": internal_links,
        "external_links": external_links,
        "images": images,
    }


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("title")
    if tag and tag.string:
        return tag.string.strip()
    return None


def _extract_meta_description(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": "description"})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _extract_headings(soup: BeautifulSoup) -> Dict[str, List[str]]:
    headings: Dict[str, List[str]] = {}
    for level in range(1, 7):
        tag_name = f"h{level}"
        found = soup.find_all(tag_name)
        if found:
            headings[tag_name] = [h.get_text(strip=True) for h in found]
    return headings


def _extract_links(
    soup: BeautifulSoup, base_url: str, base_domain: str
) -> Tuple[List[str], List[str]]:
    internal: List[str] = []
    external: List[str] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        resolved = _resolve_url(href, base_url)
        if resolved is None:
            continue

        parsed = urlparse(resolved)
        if parsed.hostname and parsed.hostname == base_domain:
            internal.append(resolved)
        elif parsed.hostname:
            external.append(resolved)

    return internal, external


def _extract_images(
    soup: BeautifulSoup, base_url: str
) -> List[Dict[str, str]]:
    images: List[Dict[str, str]] = []
    for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        if not src:
            continue
        resolved = _resolve_url(src, base_url)
        if resolved is None:
            continue
        images.append({
            "src": resolved,
            "alt": img.get("alt", "").strip(),
        })
    return images


def _resolve_url(href: str, base_url: str) -> Optional[str]:
    """Resolve a potentially relative URL against a base URL.

    Returns None for non-HTTP schemes (mailto:, javascript:, etc.).
    """
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None

    resolved = urljoin(base_url, href)
    parsed = urlparse(resolved)
    if parsed.scheme not in ("http", "https"):
        return None

    # Strip fragment
    return resolved.split("#")[0]
