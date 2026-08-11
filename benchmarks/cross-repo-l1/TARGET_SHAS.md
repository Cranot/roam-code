# Target SHAs — recorded BEFORE any compiler-corpus run

Cloned full-history on the benchmark host, 2026-07-27, into a dedicated
clone directory outside any repository under test.

| target  | repo             | HEAD SHA                                   | committer date            |
|---------|------------------|--------------------------------------------|---------------------------|
| fastapi | fastapi/fastapi  | `255b912928904e3ba5980425a54d6837c8bd1a1c` | 2026-07-24T21:15:37+00:00 |
| gin     | gin-gonic/gin    | `34dac209ffb6ef85cc78c5d217bbb7ad001d68fd` | 2026-06-27T00:48:16+08:00 |
| svelte  | sveltejs/svelte  | `44a7813730579b94004e182e5a67aab27aa9d2a6` | 2026-07-25T00:04:41+02:00 |

Engine under test: roam-code `b6a8e87f` (reports `roam, version 13.10.0`),
installed editable into a fresh venv from a `git archive HEAD` of that commit —
NOT the box's global `/usr/local/bin/roam` (13.9.0) and NOT the box checkout's
venv (13.8.0).
