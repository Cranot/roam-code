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

The homepage speaks to people choosing tools for their coding agents. Roam is
agent-first: agents use its local codebase context and static checks as they
work, while people set direction and decide what ships. Explain the problem
plainly: generated code can outpace our ability to read every line. Lead with
the agent workflow and agent setup, not a manual command checklist with agents
as an optional extra. Keep the warm, approachable visual style and put the
command catalog in the documentation.

Make the economics concrete: the CLI and MCP server are free and open source;
static checks use local compute and make no model calls. That does not make an
agent's own model usage free, including consumption of Roam results. Show paid
services and planned products separately. A connected agent may send tool
results to its provider; Roam's local analysis is not a promise that the entire
agent workflow stays offline. Setup instructions must explain how to connect
the tools and include checks in the agent's workflow, without promising that
connection alone makes those checks happen automatically.

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

From the repository root, using the locked development environment:

```sh
uv run --no-sync pytest tests/test_homepage_contract.py tests/test_docs_site_quality.py tests/test_doc_consistency.py tests/test_w462_landing_page_tool_count_drift.py -n 0
uv run --no-sync python scripts/linkcheck.py --strict
uv run --no-sync python scripts/prepush_check.py --full --workers 4
```

The homepage tests cover markup, local asset references, FAQ consistency,
legacy anchors, and executable examples in a temporary repository. The link
checker covers internal destinations and fragments, not external availability.
These checks do not certify the rendered layout, keyboard interactions, or
screen-reader experience.

For a local preview, serve only the public site directory, not the repository:

```sh
uv run --no-sync python -m http.server 4173 --bind 127.0.0.1 --directory templates/distribution/landing-page
```

This simple server previews the homepage and assets; it does not emulate
Cloudflare's extensionless routes, redirects, or response headers.

## Publishing

Use the normal Git gates and verify the exact commit's CI before production
deployment. `make site-deploy` refuses a dirty checkout and records its exact
commit. The equivalent direct command, in Bash with Wrangler available, is:

```bash
set -eu
site_status="$(git status --porcelain=v1 --untracked-files=all)"
test -z "$site_status"
site_sha="$(git rev-parse --verify HEAD)"
wrangler pages deploy templates/distribution/landing-page \
  --project-name roam-code --branch main --commit-dirty=false --commit-hash="$site_sha"
```

Publish the clean, committed site directory to the existing Pages
project, then check both the deployment URL and custom domain against that
source, including CSS, redirects, and security headers. A homepage change alone
does not need a Python package version bump or PyPI release. Keep deployment
receipts and operational handoffs in the ignored `internal/` folder.

`make site-check` compares the served changelog with the declared version; it
does not verify every page or asset. Record the source commit, deployment ID,
checked URLs, content/asset comparisons, redirects, and header results separately
from browser/device/accessibility testing. Keep the prior verified deployment
available for recovery. For a package release, use the separate
[release guide](releases.md).
