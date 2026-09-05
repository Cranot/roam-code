# Maintaining the Roam website

The public site is static HTML and CSS in
[`templates/distribution/landing-page/`](../templates/distribution/landing-page/).
It is published to [roam-code.com](https://roam-code.com/) through the existing
Cloudflare Pages project. A Git push alone does not publish it.

## Homepage

Edit [`index.html`](../templates/distribution/landing-page/index.html) for the
story and [`home.css`](../templates/distribution/landing-page/home.css) for its
layout. Homepage styles are scoped to `.home-page`; shared navigation, fonts,
and the other pages still use `landing.css`. Keep homepage-only changes out of
the shared stylesheet unless the change is deliberately site-wide.

The homepage should help someone answer three questions: what Roam is useful
for, how it fits their work, and how to try it. Lead with familiar engineering
questions, explain specialist terms where needed, and keep the command catalog
in the documentation. Show free local tools separately from paid services and
planned products.

- Label illustrative diagrams as examples, not live command output.
- Keep privacy and static-analysis limitations visible. A health score is not
  permission to merge, and a suggested test list is not coverage.
- Keep search metadata and FAQ structured data aligned with visible text.
- Use registry-derived command/tool counts; avoid hard-coded popularity counts
  or unqualified performance promises.
- Preserve existing section anchors, local fonts, social-preview assets, and
  security headers. Navigation and FAQs must work without JavaScript.
- Use responsive layouts, readable text, visible keyboard focus, and reduced
  motion support. Source checks are not a substitute for browser accessibility
  or device testing.

## Check and publish

From the repository root, with the development environment active:

```sh
python -m pytest tests/test_homepage_contract.py tests/test_docs_site_quality.py tests/test_doc_consistency.py tests/test_w462_landing_page_tool_count_drift.py
python scripts/linkcheck.py --strict
python scripts/prepush_check.py --full
```

The homepage tests cover markup, local asset references, FAQ consistency,
legacy anchors, and executable examples in a temporary repository. The link
checker covers internal destinations and fragments, not external availability.
These checks do not certify the rendered layout, keyboard interactions, or
screen-reader experience.

For a local preview, serve only the public site directory, not the repository:

```sh
python -m http.server 4173 --bind 127.0.0.1 --directory templates/distribution/landing-page
```

This simple server previews the homepage and assets; it does not emulate
Cloudflare's extensionless routes, redirects, or response headers.

Use the normal Git gates and verify the exact commit's CI before production
deployment. Publish the clean, committed site directory to the existing Pages
project, then check both the deployment URL and custom domain against that
source, including CSS, redirects, and security headers. A homepage change alone
does not need a Python package version bump or PyPI release. Keep deployment
receipts and operational handoffs in the ignored `internal/` folder.
