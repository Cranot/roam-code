# Performance budget

Caps for the static commercial site. Any change exceeding a budget should
be justified in the commit message; CI gate (planned) will fail on excess.

## Per-page caps (compressed over wire, gzipped)

| Asset             | Budget     | Current (2026-05-07)        |
|-------------------|------------|------------------------------|
| HTML, homepage    | 12 KB      | 9.8 KB                       |
| HTML, other pages | 8 KB       | 2.6 - 7.3 KB                 |
| CSS (landing.css) | 12 KB      | ~10 KB                       |
| Fonts (combined)  | 50 KB      | 42 KB (3 woff2 files)        |
| og.png            | 15 KB      | ~10 KB                       |
| favicon.svg       | 1 KB       | <500 bytes                   |
| **Total cold-load page weight** | **80 KB**  | ~57-65 KB |

## Network-time caps

| Metric                  | Budget   | Current      |
|-------------------------|----------|--------------|
| TTFB (EU client)        | 150 ms   | 60-114 ms    |
| Total response time     | 200 ms   | 60-115 ms    |
| Time to first paint     | 400 ms   | (browser)    |
| Largest Contentful Paint| 1.5 s    | (browser)    |
| Cumulative Layout Shift | 0.05     | (browser)    |

## What counts against the budget

- Inline `<script type="application/ld+json">` blocks count toward HTML
- Inline `<style>` (we use none) would count toward HTML
- External fonts: must be self-hosted, in /fonts/, woff2 only
- Images: avoid; if added, must be ≤ 20 KB compressed and lazy-loaded
- JavaScript: site is JS-free by policy; any `<script>` needs explicit
  approval and must not load third-party origins

## Verification commands

```bash
# Per-page cold-load weight
curl -s --compressed -o /dev/null \
  -w "%{size_download} bytes  TTFB %{time_starttransfer}s\n" \
  https://roam-code.com/

# All 14 pages at once
for p in / /pricing /compare /setup /about /press /changelog /status \
         /security /accessibility /no-cookies /privacy /terms /refund ; do
  curl -s --compressed -o /dev/null \
    -w "%-15s %{size_download} bytes  TTFB %{time_starttransfer}s\n" \
    "https://roam-code.com$p"
done

# CSS size (uncompressed source)
wc -c landing.css
```

## Future CI gate (planned)

A GitHub Action will run `wrangler pages deploy --dry-run` then `curl
--compressed` against each preview URL and fail if any cap is exceeded.
Until that's live, this document is the source of truth.
