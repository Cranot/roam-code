# Regression FP fixtures (D4)

Each `*.json` file in this directory describes one false-positive pattern that
roam-code's detectors used to flag (or could regress to flagging) and how to
verify the fix stays in. The harness lives at
`tests/test_regression_fp_corpus.py` — pytest parametrises every entry from
every fixture file and asserts the expected outcome.

Adding a new fixture is a one-file edit; no Python required. The schema is
small on purpose so a customer report can be turned into a fixture verbatim.

## Fixture file shape

Each file is a JSON object with `entries: [...]`. Each entry has:

- `name` — short identifier (lowercase + dashes)
- `description` — one-line context (often the source-of-record incident)
- `helper` — which detector helper to exercise. Supported:
  - `in_memory_call` — input is a call string, expect is `true` if the call
    should be treated as in-memory (cache/state) and not as I/O. Optional
    `framework` field activates a profile for this assertion.
  - `depth_guard_regex` — input is a code snippet, expect is `true` if the
    bounded-recursion regex should match.
  - `dev_only_block` — input is a code snippet, expect is `true` if
    `_is_dev_only_block` should treat the body as DEV-stripped.
  - `call_awaited` — input is `{"call": "...", "snippet": "..."}`, expect
    is `true` if the snippet awaits the call.
- `input` — value (or object) consumed by the helper
- `expect` — boolean assertion

## Why this exists

The user-reported FP batch on a Vue 3 + Laravel multi-tenant codebase
(2026-05-06) drove fixes M1-M14 and D2/D3. Without a regression corpus,
each fix lives only in code; one upstream refactor can re-break a pattern
silently. Each entry here is a tripwire — if a future change re-introduces
the FP, pytest fails by name and points at the original incident.
