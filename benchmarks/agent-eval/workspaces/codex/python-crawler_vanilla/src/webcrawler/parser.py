from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .models import Heading, ImageData

IGNORED_SCHEMES = ("mailto:", "javascript:", "tel:", "data:")


@dataclass(slots=True)
class ParsedPage:
    title: str = ""
    meta_description: str = ""
    headings: list[Heading] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    images: list[ImageData] = field(default_factory=list)
    malformed_links: list[str] = field(default_factory=list)

    @property
    def all_links(self) -> list[str]:
        return self.internal_links + self.external_links


def get_domain(url: str) -> str:
    return urlsplit(url).netloc.lower()


def normalize_url(url: str, base_url: str | None = None) -> str | None:
    candidate = (url or "").strip()
    if not candidate:
        return None
    lower = candidate.lower()
    if lower.startswith(IGNORED_SCHEMES):
        return None
    if "://" in candidate and not (
        lower.startswith("http://")
        or lower.startswith("https://")
        or candidate.startswith("//")
    ):
        return None

    try:
        joined = urljoin(base_url, candidate) if base_url else candidate
        parts = urlsplit(joined)
        if parts.scheme.lower() not in {"http", "https"}:
            return None
        if not parts.netloc:
            return None
        path = parts.path or "/"
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                path,
                parts.query,
                "",
            )
        )
    except ValueError:
        return None


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def parse_html(html: str, base_url: str, base_domain: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    parsed = ParsedPage()

    if soup.title and soup.title.string:
        parsed.title = soup.title.string.strip()

    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag:
        parsed.meta_description = (meta_tag.get("content") or "").strip()

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(" ", strip=True)
        if text:
            parsed.headings.append(Heading(level=tag.name, text=text))

    internal_links: list[str] = []
    external_links: list[str] = []
    malformed_links: list[str] = []

    for link in soup.find_all("a"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        normalized = normalize_url(href, base_url=base_url)
        if not normalized:
            if not href.lower().startswith(IGNORED_SCHEMES) and not href.startswith("#"):
                malformed_links.append(href)
            continue

        if get_domain(normalized) == base_domain:
            internal_links.append(normalized)
        else:
            external_links.append(normalized)

    images: list[ImageData] = []
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        normalized_src = normalize_url(src, base_url=base_url)
        if not normalized_src:
            continue
        images.append(ImageData(src=normalized_src, alt=(img.get("alt") or "").strip()))

    parsed.internal_links = _dedupe_preserve_order(internal_links)
    parsed.external_links = _dedupe_preserve_order(external_links)
    parsed.images = images
    parsed.malformed_links = _dedupe_preserve_order(malformed_links)
    return parsed
