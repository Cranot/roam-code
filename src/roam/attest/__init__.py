"""roam Code Graph Attestation (CGA)./E.1 chain partner for ``roam taint`` (already shipped E.2).
Per the v12 brainstorm 05_security_enterprise.md and the senior review,
this primitive becomes the moat for CRA Sep-2026 / EO 14028 / OSPS
Baseline conformance: every PR ships a signed in-toto attestation
that any CI / supply-chain scanner / AI agent can verify in 50 ms
without re-indexing.

This v12.0 scaffold ships:

* :mod:`attest.cga` — build the in-toto v1 Statement + the
  ``https://roam-code.com/spec/CodeGraph/v1`` predicate body. Merkle root
  over symbol fingerprints, edge bundle digest, language summary.
* :mod:`commands.cmd_cga` — CLI to emit and verify unsigned
  attestations.

v12.1 ships:

* Cosign keyless signing (Fulcio + Rekor)
* Embedded CycloneDX 1.7 + OpenVEX predicates
* Reachability claims integrated with ``roam taint`` output
* GitHub Artifact Attestations upload action
"""

from __future__ import annotations
