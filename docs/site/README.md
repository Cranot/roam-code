# roam-code Website

This directory is the static GitHub Pages artifact deployed to `https://cranot.github.io/roam-code/`.

## Page structure

| File | Purpose |
|------|---------|
| `index.html` | Product landing page (what roam-code is, features, quick start) |
| `getting-started.html` | Step-by-step tutorial from install to CI gates |
| `integration-tutorials.html` | End-to-end MCP client tutorials (Claude, Cursor, Gemini, Codex, Amp) |
| `command-reference.html` | Task-oriented command reference with examples |
| `architecture.html` | System architecture guide + diagram |
| `landscape.html` | Competitive landscape dashboard (interactive competitor map) |
| `docs.css` | Shared docs styles for tutorial/reference/architecture pages |
| `landing.css` + `landing.js` | Styles and scripts for the landing page |
| `styles.css` + `app.js` | Styles and scripts for the competitive landscape page |
| `data/landscape.json` | Generated competitor data (do not edit by hand) |

## Publish path

- Workflow: `.github/workflows/pages.yml`
- Artifact root: `docs/site`
- Expected URL: `https://cranot.github.io/roam-code/`

## Update workflow

### Landing page

Edit `index.html`, `landing.css`, or `landing.js` directly and commit.

### Documentation pages

Edit `getting-started.html`, `integration-tutorials.html`, `command-reference.html`, `architecture.html`, and `docs.css`.
Keep command examples aligned with the current CLI surface in `README.md`.

### Competitive landscape data

1. Update source tracker first: `reports/competitor_tracker.md`
2. Regenerate JSON: `python src/roam/competitor_site_data.py --out docs/site/data/landscape.json`
3. Verify sync: `python src/roam/competitor_site_data.py --check --out docs/site/data/landscape.json`
4. Commit tracker + generated JSON together (tests enforce this)
