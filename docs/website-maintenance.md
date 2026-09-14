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

The checkout illustration has an optional native disclosure showing selected
fields from a real CLI run on a synthetic four-function project. Its full dated
response is `data/examples/checkout-impact-2026-09-13.json` under the site source.
`test_homepage_walkthrough_and_connection_example_execute` rebuilds that fixture,
checks the displayed fields against both the captured response and a new result,
and exercises the linked explicit-patch review recipe. Keep the capture date,
synthetic scope, omitted-field label and full-response link visible. The small
fixture's high relative-risk score is explained, not hidden or represented as
production evidence. A future capture is a new observation; do not silently
rewrite the dated one or turn a successful indexed traversal into runtime coverage.

The hero's example link leads to that existing section; the map keeps its own
explore link. Check both routes without JavaScript before adding another demo
surface. A captured fixture demonstrates a command's output, not agent-workflow
savings or detector accuracy on unfamiliar repositories.

## Shared typography, navigation, and writing

`landing.css` owns the site's design foundation. All HTML routes load it;
`home.css` and `atlas.css` provide scoped layouts, not separate color themes.
Every page, including the full atlas and homepage map, shares the warm paper
background, white cards, blue accents, and blue-black ink. Bind atlas colors to
the shared tokens so a site-wide palette change reaches both map presentations.

The sitemap omits optional `lastmod` dates until there is a maintained per-page
date source. Do not stamp all routes with a release date or keep historical
dates through a page rewrite. Keep setup-directory labels independent of the
number of supported integrations; the destination matrix owns that list.

- Use `--font-sans` (self-hosted Space Grotesk) for prose, navigation and
  headings; use `--font-mono` (self-hosted IBM Plex Mono) for commands, code
  and compact technical metadata. Font-face declarations remain in one file.
  Preserve the generic fallbacks in these tokens; block font downloads in a
  browser check so code samples remain monospace when a font is unavailable.
- Use the shared `--t-*` scale. Body prose and card descriptions use
  `--t-base` (`1rem`); labels, controls and code use at least `--t-sm`
  (`0.875rem`). Reserve smaller metadata for nonessential secondary details.
  Keep text sizing in `rem`, with a unitless reading line-height; avoid inline
  typography and fixed-pixel body text. SVG graph labels use viewBox coordinates,
  so their apparent size depends on the graph; retain the HTML selection/details
  alternative and do not claim a rendered minimum from SVG font-size alone.
  The full atlas keeps a 60rem minimum canvas inside its own named, keyboard-
  focusable horizontal scroller. Both atlas presentations have a native area
  chooser; the full map pans horizontally to the chosen area without moving
  the page. Anchor outer graph labels inward so the SVG does not clip them.
  Give related view buttons a named group. In forced-colors mode, keep dimmed
  connections visible and distinguish selected connections by line style and
  weight as well as color; ordinary inline dimming must not defeat that rule.
- Share the hero and section scales across product pages. Article titles use
  the smaller `--t-doc-title`; dense reference pages need a reading hierarchy,
  not a full marketing hero. Keep prose measures bounded, code blocks readable,
  and long identifiers wrappable or horizontally scrollable.
  Keep documentation tables native: one grid for the header and body, inside
  a named `.table-wrap` with `tabindex="0"`. Do not turn `thead` and `tbody`
  into separate tables. Keep the generated command-reference appendix in sync
  with `dev/build_command_reference.py`; never hand-edit its generated rows.
  Let single-column grids shrink with `minmax(0, 1fr)`, and test long tokens
  with enlarged text instead of relying on page-level overflow clipping.
- Keep the primary navigation identical: Explore the map, Docs, Compare,
  Pricing, GitHub, Set up your agent. Generate it with
  `scripts/build_site_navigation.py --write`; the default command checks drift
  without writing. It scans every site HTML page and refuses a missing or
  ambiguous primary navigation before writing any page. The script owns only
  that navigation block, including current-page/section state, the logo,
  and the labelled no-JavaScript menu control.
- At narrow widths the primary navigation is in normal flow. The docs subnav
  stays in normal flow at every width: a fixed second sticky offset cannot
  account for a wrapped or enlarged primary menu. Keep keyboard focus distinct
  from the current-page state throughout the site.
  Keep `main#main` focusable with `tabindex="-1"`: activating the skip link
  must move keyboard focus into content, without adding a routine Tab stop.
- Lead with the reader's question or next action. Use sentence case and short
  descriptions; explain CLI/MCP on entry pages rather than making readers learn
  the terminology first. Prefer “a record of the checks” to unexplained
  “proof packets,” and “what else uses this?” to “blast-radius assurance.”
  Preserve precise command names and field names in technical reference text.
- Explain the reason to add Roam to an existing agent: query indexed code
  relationships and run mechanical checks, then use those results to choose
  where to read, which implementation choices to investigate, and what to check.
  Use [the product model](understanding-roam.md#the-product-model-behind-the-words)
  as the shared meaning across surfaces. Do not reduce it to navigation,
  finding bugs, or post-edit review, or imply Roam chooses and ships fixes.
  Explain refresh when describing reuse. Keep the model's own usage separate
  from the free static checks.
- Give each surface its own job. The homepage introduces the mechanism and a
  concrete example; setup turns interest into a working agent routine; the
  atlas illustrates connections; pricing states availability and terms.
  Review each changed page in full, including secondary copy, then synchronize
  metadata, visible FAQs and JSON-LD, and `llms.txt`. Consistency is shared
  meaning, not the same slogan on every page. Preserve good existing copy.
  Include package descriptions and registry cards in claim reviews. Verify
  named guides and command descriptions against their destinations; a resolving
  link alone does not establish that the promised guidance exists there.
  A cold reading can expose missing explanations, but model preferences and
  phrase-based source tests are not evidence of human comprehension or sales.
- Keep playfulness in invitations and examples, not in safety guarantees.
  Label illustrative outputs and dated comparisons; qualify setup timing,
  incomplete scans and static-analysis limits. A URL visit cannot confirm a
  payment or booking. Written confirmation and agreed terms are authoritative.
  Do not change prices or contract terms as a copy-editing shortcut.
  `/status` is a manual directory of services, not live monitoring. A wording
  review date is not an uptime measurement. Keep unmeasured states neutral;
  do not add green operational badges or incident-free claims without evidence.
- Match page titles and social previews to the visible introduction. Keep the
  press palette in sync with CSS tokens. Changelog prose is historical: repair
  formatting through `scripts/build_changelog_html.py`, never hand-edit its
  generated body or rewrite past release claims as current measurements.

`tests/test_site_coherence.py` checks every HTML route's nesting, generated
navigation, accessible menu relationships, relative shared type, and the press
palette. It also protects literal code spans in changelog generation. These
are source contracts, not browser layout, contrast, zoom, or accessibility
certification. Rendered desktop/mobile, keyboard, text-zoom and assistive
technology checks remain separate evidence.

## Homepage wording

The homepage speaks to people choosing tools for their coding agents. Roam is
agent-first: agents use its local codebase context and static checks as they
work, while people set direction and decide what ships. Choose a problem-led
or offer-led opening according to the reader's question. When discussing code
volume, explain the premise plainly: generated changes can outpace line-by-line
attention. An opening can instead lead with the analysis and checks Roam adds.
Lead with the agent workflow and agent setup, not a manual command checklist with agents
as an optional extra. Keep the warm, approachable visual style and put the
command catalog in the documentation.

Make the opening useful without the illustration. Name a question the tools
help answer, such as “what could this change affect?”, then explain the work:
find relevant files, evaluate an implementation, and check changes. An example
must illustrate the product without becoming its entire identity. A “bigger
picture,” “find problems,” or “better solutions” slogan alone does not explain
why to connect Roam to an existing coding agent. Read the headline and lede
without the rest of the page before accepting that explanation. Keep impact conditional;
the graph is not a complete prediction of runtime behavior. Describe repeat use
with an explicit index refresh, not a map that stays current by itself.

Separate the agent's job from Roam's contribution. An opening that merely
promises understanding, problem-finding, or better solutions could describe the
coding agent itself. Name the added analysis or check and the result the agent
can use. A plain category is useful when the supporting text explains the
mechanism; neither another benefit verb nor a feature count supplies that reason.
Check a healthy-code case so defect discovery does not become the only value.

Keep the whole page broader than navigation or post-edit review. Its progression
is a code question, several different engineering jobs, a concrete algorithmic
alternative, the local-compute boundary, and a useful first task. Preserve the
hero atlas and illustrative caller diagram without making every lower section
another explanation of the map. `roam algo` (legacy alias `roam math`) supplies
candidate alternatives as well as findings; it does not establish that a
replacement is correct or faster. In the repeated-lookup example, retain the
checks for value types, collection changes, ordering, duplicates, and returned
positions, followed by behavior tests and performance measurement. Keeping the
existing code is a valid outcome when an alternative does not preserve required
behavior or offer a useful gain on the workload. Label the
example illustrative, not a captured finding. Explain the CLI route and the
MCP preset requirement rather than implying the default core preset exposes it.

Give adjacent sections distinct jobs: let the question list invite exploration,
the worked algorithm example explain an alternative and its conditions, and the
install section own connection instructions. Bound prominent no-model-call claims
to static checks in the heading itself, not only an eyebrow or distant FAQ.
Keep the primary setup action close to the opening explanation; rebalance the
hero columns before shrinking shared typography or hiding qualifications to fit.
Check the resulting reading order and CTA placement in the browser at narrow
desktop widths as well as mobile. A particular fold position is not a conversion
measurement or a reason to remove the map's scope notes.

The install section should lead to a first result, not stop at configuration.
Link to the existing `docs/integration-tutorials.html#validate-connection`
checklist: confirm the project and connected tools, inspect a known function's
returned source locations, and keep partial or missing evidence explicit.
The setup page also owns a visible `#first-result` step, after installation and
connection. It should work as a complete route when reached without reading the
homepage. The docs index routes readers to a tutorial, task reference, or recovery
guide rather than introducing another product slogan. When reorganizing these
pages, follow their actual links: fewer words do not help if a prerequisite or
useful destination disappears.

Use the same meaning in the homepage, README opening, About, setup, press copy,
and agent-readable `llms.txt`, without repeating the same slogan on every page.
The atlas is a source-derived illustration; agents receive results from the
tools, not by navigating the showcase. Do not turn detector leads into promises
to catch bugs other tools miss, or evidence records into proof of who acted.

Qualify product meaning separately from capability claims, technical site checks,
and owner acceptance. A passing source test, full test suite, or successful
deployment does not establish that the opening communicates the right product.
For a material repositioning, retain a cold reading without the writer's notes
alongside source-informed review; an unavailable reading remains unavailable,
not an inferred endorsement. Neither model review nor source lints establish
human comprehension or conversion. An unresolved owner objection keeps the
affected copy unresolved even when its factual assertions and markup pass.
When the owner explicitly delegates evaluation and application, record the
author's decision and its limits rather than inventing independent acceptance
or waiting for an approval the owner delegated. Preserve the selected text and
compare it with the served H1 and metadata after applying it. Corrected guidance
or a private draft is not a corrected website; report local and live states
separately, and retain the normal publication gates.

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

### Runnable agent guidance

Keep the worked change-review sequence in
`templates/distribution/landing-page/docs/canonical-demo.html` and link to it
from conceptual outlines. Start and bind the run, initialize collection,
then gather full JSON evidence. Refresh required saved detector evidence before
reviewing the intended patch. Name the same run explicitly when closing and
verifying it, and keep strict-emission refusals visible. Executing a test does
not automatically add a test record to a bundle; an intact ledger does not
establish successful work, complete coverage, identity, or permission to ship.

MCP setup examples must distinguish a configuration preview, an explicit write,
an installed process's inventory, and the tools exposed by the connected client.
Route short setup links to the complete client guide rather than showing a
preview command as a finished connection. Keep the shared connection check and
agent routine before optional CI, gateway and audit-export sections, with native
links from each client section. A preflight report informs the next change; it
does not block an edit or PR by itself. Preserve that distinction from explicit
gating options such as `health --gate`.
Use the actual preset and argument schemas. A retry after a tool failure must
respect uncertain write outcomes. Result guidance should distinguish incomplete
delivery from incomplete analysis: a complete requested projection can answer a
bounded question without fetching unrelated pages, but a zero-result claim needs
evidence that the intended scan completed.

Use package module entry points in instructions for installed users. Source-only
scripts need an explicit checkout prerequisite. Keep CLI flags separate from MCP
argument names, generated filenames exact, and signing formats distinct. Replace
invented success output with an explicitly illustrative scenario or a command
whose observed result and limitations can be inspected. The guidance contracts
in `tests/test_site_guidance_contract.py` cover known mistakes; passing these
checks is not a universal writing score or proof of a live agent connection.

## Code atlas showcase

The dedicated `/explore` page lives in `explore.html`, with page-scoped
`atlas.css`. The homepage hero embeds a compact interactive version, with a
native SVG fallback and a link to the full atlas. Its illustrative checkout
example follows below as a separate explanation, not another source-derived
map. Hero framing and responsive layout belong in `home.css`; the homepage
contains only one atlas instance. `atlas.mjs` progressively enhances both maps;
`atlas-model.mjs` owns validation and direct dependency selection. No external
scripts, model calls, analytics, storage, or repository uploads are involved.
Navigation and the generated `atlas-map.svg` fallback work without JavaScript.
Loading failure preserves that fallback and offers an explicit retry. The
interactive request has a 15-second deadline covering headers and the JSON
body; expiry requests cancellation and releases the UI to retry. Browser timer
scheduling can be delayed in background tabs, so this is an interaction budget,
not a wall-clock service guarantee. Late results from a timed-out attempt cannot
replace a recovered map. Multiple widgets share one pending request.

Keep the retry button visible while its request is pending, with `aria-disabled`
and a handler guard against repeated activation. On success, move focus to the
area chooser without scrolling only if the retry button still has focus; never
pull a reader back from another link or control. Loading, failure and readiness
messages use the existing status region. These are progressive enhancements;
native navigation and the static map remain independent of the data request.

The atlas uses the same light palette as the rest of the site, including its
navigation, controls, inspector, and homepage preview. Use a filled blue node
for the selected area, pale blue nodes for its direct connections, and dark
labels on the white map surface. De-emphasize unrelated areas without reducing
the opacity of their text. The dashed blue selection ring settles once, with
no ongoing animation or implied live scan; the solid warm-colored focus ring
remains distinct from selection. Respect reduced-motion preferences.

The generated static SVG is an external image and cannot inherit page CSS.
Update its paints through `scripts/build_atlas_data.py` when changing the
palette, then regenerate it so loading, retry, and no-script states stay light.
`tests/test_atlas_site.py` checks shared token bindings, foreground/background
contrast calculations, and fallback paints. These checks do not measure
rendered contrast, certify accessibility, or replace browser verification.

The dataset is a **working-tree source snapshot**, not native `roam map`
output or the indexed call graph. `scripts/build_atlas_data.py` reads Python
AST imports under `src/roam`, resolves literal local modules, deduplicates
directed module pairs, and groups them by directory. Conditional and typing
imports count; dynamic imports, other languages, and symbol-level calls do not.
Keep those limits visible beside the map. Do not relabel incoming imports as
verified runtime blast radius. Do not infer independence from absent edges.

The full atlas also names direct relationships in visible “Imports from” and
“Imported by” lists beside the selected area. Drive these from the same validated
snapshot as the drawn edges; preserve both directions for reciprocal pairs and
qualify empty lists as empty in this snapshot. Keep the lists outside the concise
live status region so changing an area does not automatically announce every
neighbor. The compact homepage preview retains its shorter presentation.

Regenerate before intentionally updating the source snapshot:

```sh
uv run --no-sync python scripts/build_atlas_data.py
uv run --no-sync python scripts/build_atlas_data.py --check
node --check templates/distribution/landing-page/atlas-model.mjs
node --check templates/distribution/landing-page/atlas.mjs
node --test tests/atlas_model.test.mjs tests/atlas_interaction.test.mjs
uv run --no-sync pytest tests/test_atlas_site.py tests/test_homepage_contract.py -n 0
```

The generator refuses an empty or unparsed source corpus. The JSON records
file/connection denominators, the unresolved local import count and a source
digest. Coordinates and area descriptions are editorial layout, not graph
distance or inferred architecture. The static SVG and interactive rendering
share the dataset. The snapshot check is intentionally separate from generic
tests: changing Python source does not silently rewrite the public snapshot.
Use the site tests and link checker below; source checks do not certify browser,
touch or assistive-technology behavior. Preserve the existing Cloudflare Pages
release gates; this page does not create a separate hosting project.

The `Site atlas contracts` CI job runs the JavaScript syntax, model and interaction checks
on Node 26.2.0 with no package installation or dependency cache. Validation
rejects duplicate directed edges, coincident connected coordinates, off-canvas
coordinates, unsafe counts, understated connection totals, and missing metric
definitions. Reciprocal edges, isolated areas, and additional within-area
imports remain valid. The interaction tests execute the real `atlas.mjs` module
with a small DOM double, mocked fetch, and manually advanced timers. They cover
request/body failures, deadlines, shared and repeated retries, stale results,
focus requests, keyboard activation, and area/filter event handlers without a
network request or real-time wait. They do not establish rendered layout,
native focus behavior or assistive-technology announcements; those still need
browser testing. The Python site tests cover markup and generated assets.

## Existing selling surface

Keep this path coherent rather than adding a second funnel:

| Reader's question | Existing surface | Next action |
| --- | --- | --- |
| What will this give my coding agent? | Homepage and README | Free agent setup |
| What is free and what can I pay for? | `pricing.html` | Setup or the existing PR Replay scope |
| What would the report contain? | `audit.html` and the linked self-audit example | Local sample or an email enquiry |
| What happens before work starts? | Report scope, security information, legal templates | Agree scope, access, availability, and written terms |

Pricing leads with the available free tools and existing paid report, not an
unavailable hosted product. Keep Review and Cloud visibly **planned, not
available to subscribe to**, including inside cards, FAQs, and `llms.txt`.
Preserve their previously published proposed rates as secondary reference;
do not convert them into live billing or support promises. Historical proposals
are not evidence of customers, popularity, demand, retention, or a launch date.

The paid report buys review of a named scope and a deliverable, not access to
locked local analysis. Do not imply incident prevention, runtime test coverage,
or authenticated historical approval from static replay. Label the linked Roam
self-audit as an example, not a customer case study. The sample command defaults
to `HEAD~5..HEAD`, not five pull-request identities; explain the required Git
history and keep install and invocation on separate lines for shell portability.

Contact buttons open email drafts. Keep a readable address as fallback and ask
for the question, not source code, credentials, or private reports. Do not send
test enquiries to the inbox or add analytics, forms, or checkout as routine polish.
Confirm availability by email; do not invent response-time guarantees or scarcity.

On the report page, put the sample and paid choices before the detailed
methodology. Each paid card should state its deliverable, walk-through, and
turnaround from the agreed kickoff. Keep exclusions visible before contact.
Label linked historical examples with their date; they are neither current
measurements nor customer proof. Preserve their original contents.

The email draft should ask for the question, languages/framework, optional
public repository link, and proposed change window. A 90-PR scope is not a
90-day default. A private-access discussion comes after that first enquiry;
the wording must not imply that sending an email commits the buyer to a purchase.

Prices, credit windows, refunds, scope, and data handling must agree across copy,
structured metadata, and reviewed written terms. When existing sources conflict,
record the conflict privately and resolve it with the owner before publication;
do not silently change commercial terms. Legal templates with placeholders are
templates, not executed agreements or evidence of business readiness.

Styles for the existing pricing/report pages are scoped to `.selling-page` in
`landing.css`. Preserve the homepage's own styling and existing route/fragment
links. FAQ structured data must match every visible answer. Keep unavailable
pricing details in native disclosures that remain usable without JavaScript.
Wide comparison tables need a keyboard-focusable scroll wrapper (`tabindex="0"`),
a named `region` linked to the table caption, and explicit row/column header
scopes. Reuse the shared focus-visible styling. Source checks enforce that
markup; actual keyboard scrolling and assistive-technology behavior still need
browser testing.

## Check and publish

The atlas source digest normalizes CRLF to LF before hashing sorted relative
paths and Python source bytes. Its `source_hash_definition` names that
representation: it is portable across Git checkout line endings, not a raw-byte
checksum of the checkout. Other source-byte changes still change the digest.

The metadata gate preserves the atlas SVG's two reviewed, exact accessibility
text elements. Other titles/descriptions and authoring metadata remain checked;
a filename or ARIA attribute does not exempt them. When changing this accessible
copy, review the replacement and update the exact elements in
`scripts/strip_metadata.py`, keeping the mixed benign/private metadata controls
in `tests/test_svg_accessibility_metadata.py`. Do not remove the accessible name
and description merely to satisfy the metadata gate.

Review the skimmed page as well as its full prose: headings, action labels and
diagrams can imply a stronger result than the nearby explanation. Trace pictured
operations to current behavior and label illustrative output. Keep the command
reference's `mutate` and `simulate` anchors, but describe supported edit previews
and requested graph operations, not a guaranteed compiler or automatic architecture
search. Homepage command actions should reach guidance for that command rather
than the top of a long appendix. Regenerate owned CLI/MCP descriptions after
changing their source; matching a phrase is a regression guard, not writing quality.

From the repository root, using the locked development environment:

```sh
uv run --no-sync pytest tests/test_site_coherence.py tests/test_site_guidance_contract.py tests/test_homepage_contract.py tests/test_atlas_site.py tests/test_selling_surface_contract.py tests/test_docs_site_quality.py tests/test_doc_consistency.py tests/test_w462_landing_page_tool_count_drift.py tests/test_product_copy_claim_boundaries.py tests/test_site_staging.py tests/test_site_review_regressions.py -n 0
uv run --no-sync python scripts/build_site_navigation.py
node --test tests/atlas_model.test.mjs tests/atlas_interaction.test.mjs
uv run --no-sync python scripts/linkcheck.py --strict
uv run --no-sync python scripts/prepush_check.py --full --workers 2
```

The homepage and selling-surface tests cover markup, local asset references, FAQ consistency,
legacy anchors, and executable examples in a temporary repository. The link
checker covers internal destinations and fragments, not external availability.
These checks do not certify the rendered layout, keyboard interactions, or
screen-reader experience.

The Press fact list is owned by `scripts/sync_surface_counts.py`, including
its bold-number HTML and default-preset count. Test a changed count through the
real check/write path, not just today's agreement between prose and source.
`tests/test_press_count_sync.py` does this in an isolated copy. Keep historical
measurements separate from current source counts, with their original evidence
and scope; do not place unverified download or subsystem estimates under one
"authoritative" heading. Installed-package identity is a separate observation.

### Search and sharing surfaces

Review the actual sharing image as well as its HTML metadata. The current
`data/social-preview.json` records its visible wording, dimensions, and SHA-256.
The hash binds a visually reviewed bitmap; it is not OCR or an independent
wording assessment. When the message changes, inspect the replacement at full
and reduced size, update the record and every OG/Twitter reference, and use the
image's actual dimensions. A new asset URL avoids keeping superseded text at
the same sharing URL. Preserve old inbound image links with a redirect and check
that redirect on Pages; a basic local file server does not emulate it.

Keep the sitemap equal to the intended indexable HTML pages, not every route
that happens to return 200. The receipt and error pages remain `noindex` and
outside the sitemap. This source census does not establish what Google has
crawled or indexed. The strict link checker currently excludes `changelog.html`,
so its page denominator is not the complete source inventory. Check generated
changelog metadata separately: omit an optional modification date when no
maintained source can keep it accurate, rather than assigning a fresh date to
unchanged content. Do not invent ratings or structured-data eligibility.

The command-reference appendix is owned by `dev/build_command_reference.py`.
It uses complete first docstring paragraphs and stable `#command-<name>` links,
including labelled aliases; it is not terminal-width short help. Correct the
owner and regenerate instead of patching generated rows. Source-derived text
still needs claim review. Verify native section links on long example pages
with a keyboard and a narrow viewport, not just an HTML fragment census.

Run the associated regression controls in addition to the website suite:

```sh
uv run --no-sync pytest tests/test_command_reference_generator.py tests/test_site_seo_contract.py tests/test_site_social_preview.py tests/test_site_public_claim_scope.py tests/test_site_scenario_navigation.py tests/test_site_algorithm_example.py -n 0
```

These guards pin known failures: missing/truncated descriptions, incorrect
indexable-page selection, image-reference drift, overstated evidence claims,
and broken scenario navigation. They do not measure search rankings, conversion,
detector accuracy, legal compliance, or the quality of every sentence. Trust
summaries must preserve the underlying contract: collected development checks
are not lifetime AI-system logs, preparation is separate from signing, a local
HMAC key does not independently identify a person, and a coordination lease is
not approval. Keep operational targets distinct from contractual commitments.

The algorithm disclosure in the command reference contains an executable
synthetic fixture and selected fields from a dated CLI result. Its regression
runs the published source against the detector and uses Node.js for behavior
controls, including a counterexample to replacing every `indexOf` with a Set.
Record a missing Node runtime as a skipped semantic check. Do not present this
small fixture as a workload benchmark or a detector-accuracy study.

For an accumulated package-release candidate, the structural `--full` gate
does not replace the full non-slow test suite required by
`scripts/prepush_check.py --release --workers 2`. Follow the release guide and
record incomplete, skipped, and blocked checks separately from passing checks.

For a local preview, serve only the public site directory, not the repository:

```sh
uv run --no-sync python -m http.server 4173 --bind 127.0.0.1 --directory templates/distribution/landing-page
```

This simple server previews the homepage and assets; it does not emulate
Cloudflare's extensionless routes, redirects, or response headers.

## Publishing

Check missing routes at more than one URL depth. Pages serves the root error
document at the requested missing URL, so its shared stylesheet and recovery
links must remain root-relative. A root-level 404 check cannot catch a broken
stylesheet under `/docs/missing/page`. Verify the actual 404 status, CSS MIME
type and rendered recovery links separately.

Treat browser manifests and discovery cards as public copy and delivery
surfaces. Keep `manifest.webmanifest` focused on available tools, not planned
paid products. The extensionless MCP discovery card has an exact-path JSON
Content-Type rule in `_headers`; preserve the inherited security/cache rules
without duplicating header values or changing unrelated routes. Validate the
served MIME type and bytes on both hostnames rather than inferring them from
valid local JSON.

Use the normal Git gates and verify the exact commit's CI before production
deployment. `uv run --no-sync make site-deploy` supplies the locked interpreter
to the Makefile recipe, refuses a dirty checkout, and records its exact
commit. It uses `scripts/stage_site.py` to export committed website bytes into
an isolated upload directory. Ignored local caches and environment files never
enter that export; local files are preserved. The equivalent direct command,
in Bash with Wrangler available, is:

```bash
set -eu
site_status="$(git status --porcelain=v1 --untracked-files=all)"
test -z "$site_status"
site_sha="$(git rev-parse --verify HEAD)"
site_stage="$(uv run --no-sync python scripts/stage_site.py "$site_sha")"
wrangler pages deploy "$site_stage" \
  --project-name roam-code --branch main --commit-dirty=false --commit-hash="$site_sha"
```

The staging helper refuses changed HEAD, uncommitted files (including untracked
files hidden by Git status configuration), symlinks, private paths, an empty
site, and Git archive rules that omit or transform committed bytes. For the
archive command only, it overrides host line-ending settings with
`core.autocrlf=false` and `core.eol=lf`; it does not change saved Git settings
or the status checks' interpretation of the working tree. Attribute-driven
omissions or transformations still fail the committed-blob comparison. It records
each file's size and SHA-256 beside the export under `internal/site-deploy/`;
the manifest is outside the uploaded directory. Staging establishes committed
byte identity, not independent review, CI acceptance, or a deployment.

Publish that clean, committed export to the existing Pages
project, then check both the deployment URL and custom domain against that
source, including CSS, redirects, and security headers. A homepage change alone
does not need a Python package version bump or PyPI release. Keep deployment
receipts and operational handoffs in the ignored `internal/` folder.

The site uses stable asset filenames, so `_headers` requests cache revalidation
for HTML, stylesheets, modules, fonts, and the atlas snapshot. Browsers can reuse
unchanged files through ETags without retaining an old stylesheet for a day
after new markup ships. Keep one Cache-Control policy per response: Pages joins
repeated header values instead of treating later rules as an override. See the
[Pages serving guide](https://developers.cloudflare.com/pages/configuration/serving-pages/)
and [header rules](https://developers.cloudflare.com/pages/configuration/headers/).
Changing local headers cannot evict a response already cached under an earlier
policy; record that transition separately when checking a deployment.

Check the custom domain independently of the Pages deployment hostname. A
Cloudflare zone-level Browser Cache TTL can override a smaller origin value;
the intended setting is **Respect Existing Headers**. Pages deploy permission
does not imply zone-settings permission. Record an inaccessible or unchanged
setting as an open cache-policy issue, without expanding token permissions as
part of routine publication. Even after a correction, previously cached browser
responses can survive their old lifetime.

Keep raw response comparisons when Cloudflare transforms HTML, such as email
address obfuscation. If bytes differ, identify the exact transformation and
compare the remaining content; do not strip arbitrary markup until hashes match.
Verify the rendered address and contact link in a browser too. An explained
transformation is separate from raw-byte identity and from cache-policy success.

`make site-check` compares the served changelog with the declared version; it
does not verify every page or asset. Record the source commit, deployment ID,
checked URLs, content/asset comparisons, redirects, and header results separately
from browser/device/accessibility testing. Keep the prior verified deployment
available for recovery. For a package release, use the separate
[release guide](releases.md).
