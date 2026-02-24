"""HTML parsing and content extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

from webcrawler.url_utils import get_domain, is_valid_url, normalize_url, resolve_url


@dataclass
class PageContent:
    """Extracted content from a web page."""

    url: str
    title: Optional[str] = None
    meta_description: Optional[str] = None
    headings: dict[str, list[str]] = field(default_factory=lambda: {
        'h1': [], 'h2': [], 'h3': [], 'h4': [], 'h5': [], 'h6': []
    })
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)


class HTMLParser:
    """HTML parser for extracting page content and links."""

    def __init__(self, base_domain: str):
        """
        Initialize HTML parser.

        Args:
            base_domain: Base domain to determine internal vs external links
        """
        self.base_domain = base_domain

    def parse(self, html: str, page_url: str) -> PageContent:
        """
        Parse HTML content and extract relevant information.

        Args:
            html: HTML content as string
            page_url: URL of the page being parsed

        Returns:
            PageContent object with extracted data
        """
        soup = BeautifulSoup(html, 'html.parser')
        content = PageContent(url=page_url)

        # Extract title
        title_tag = soup.find('title')
        if title_tag:
            content.title = title_tag.get_text(strip=True)

        # Extract meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            content.meta_description = meta_desc['content']

        # Extract headings
        for level in range(1, 7):
            heading_tag = f'h{level}'
            headings = soup.find_all(heading_tag)
            content.headings[heading_tag] = [
                h.get_text(strip=True) for h in headings if h.get_text(strip=True)
            ]

        # Extract links
        for link_tag in soup.find_all('a', href=True):
            href = link_tag['href']
            # Resolve relative URLs
            absolute_url = resolve_url(page_url, href)

            # Skip invalid URLs and non-http(s) schemes
            if not is_valid_url(absolute_url):
                continue

            # Normalize URL
            normalized = normalize_url(absolute_url)

            # Classify as internal or external
            link_domain = get_domain(normalized)
            if link_domain == self.base_domain:
                if normalized not in content.internal_links:
                    content.internal_links.append(normalized)
            else:
                if normalized not in content.external_links:
                    content.external_links.append(normalized)

        # Extract images with alt text
        for img_tag in soup.find_all('img'):
            img_data = {
                'src': '',
                'alt': img_tag.get('alt', ''),
            }

            # Get image source
            if img_tag.get('src'):
                img_src = resolve_url(page_url, img_tag['src'])
                img_data['src'] = img_src

            if img_data['src']:  # Only add if we have a source
                content.images.append(img_data)

        return content
