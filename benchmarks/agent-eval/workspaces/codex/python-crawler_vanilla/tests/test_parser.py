from webcrawler.parser import get_domain, normalize_url, parse_html


def test_normalize_url_handles_relative_and_fragments() -> None:
    assert (
        normalize_url("/about#section", base_url="https://example.com/path")
        == "https://example.com/about"
    )
    assert normalize_url("mailto:someone@example.com") is None
    assert normalize_url("javascript:void(0)") is None


def test_parse_html_extracts_expected_fields() -> None:
    html = """
    <html>
      <head>
        <title>Example</title>
        <meta name="description" content="An example page" />
      </head>
      <body>
        <h1>Main</h1>
        <h2>Sub</h2>
        <a href="/internal">Internal</a>
        <a href="https://external.test/page">External</a>
        <a href="ht!tp://bad-url">Bad</a>
        <img src="/image.png" alt="sample image" />
      </body>
    </html>
    """
    parsed = parse_html(
        html=html,
        base_url="https://example.com",
        base_domain=get_domain("https://example.com"),
    )

    assert parsed.title == "Example"
    assert parsed.meta_description == "An example page"
    assert [item.level for item in parsed.headings] == ["h1", "h2"]
    assert parsed.internal_links == ["https://example.com/internal"]
    assert parsed.external_links == ["https://external.test/page"]
    assert parsed.images[0].src == "https://example.com/image.png"
    assert parsed.images[0].alt == "sample image"
    assert parsed.malformed_links == ["ht!tp://bad-url"]
