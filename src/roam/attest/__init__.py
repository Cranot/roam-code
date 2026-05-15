"""roam Code Graph Attestation (CGA)./E.1 chain partner for ``roam taint`` (already shipped E.2).
Per the v12 brainstorm 05_security_enterprise.md and the senior review,
this primitive becomes the moat for CRA Sep-2026 / EO 14028 / OSPS
Baseline conformance: every PR ships a signed in-toto attestation
that any CI / supply-chain scanner / AI agent can verify in 50 ms
without re-indexing.

Currently ships:

* :mod:`attest.cga` — build the in-toto v1 Statement + the
  ``https://roam-code.com/spec/CodeGraph/v1`` predicate body. Merkle root
  over symbol fingerprints, edge bundle digest, language summary.
* :mod:`commands.cmd_cga` — CLI to emit and verify attestations,
  with optional Cosign keyless signing (Fulcio + Rekor).
* CycloneDX 1.7 + OpenVEX predicate embedding.
* Reachability claims integrated with ``roam taint`` output.
"""

from __future__ import annotations
