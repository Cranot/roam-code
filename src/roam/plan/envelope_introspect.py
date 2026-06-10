"""Canonical compile-envelope introspection — the single source of truth for
"what is in this envelope / is it a good L1 result."

Why this exists (dogfood): THREE diagnostic commands
(compiler-corpus, dispatch-trace, and ad-hoc checks) independently re-derived
"is this L1 / probe-empty / which families." `compiler-corpus` drifted into a
real bug — it checked ``agent_contract.facts`` when the probe data lives in
``plan.prefetched_facts``, flagging every L1 envelope as empty. Centralizing
the derivation means one fix fixes all, and new diagnostics can't
re-introduce the class of bug.

A compile envelope has the inner-artifact shape ``{plan, schema,
schema_version}`` and sometimes a wrapping ``{summary, artifact, plan, ...}``
(the outer CLI envelope). This helper reads both.
"""

from __future__ import annotations


def _meta_key(k: str) -> bool:
    """True for annotation keys that are NOT real probe families."""
    return k.endswith("_definition") or k.endswith("_unavailable")


def probe_families(env: dict) -> list[str]:
    """Sorted list of SUBSTANTIVE probe-family keys in the envelope's
    ``plan.prefetched_facts`` (annotation keys excluded)."""
    if not isinstance(env, dict):
        return []
    plan = env.get("plan")
    pf = plan.get("prefetched_facts") if isinstance(plan, dict) else None
    if not isinstance(pf, dict):
        # Some callers pass the prefetched_facts dict directly.
        pf = env.get("prefetched_facts") if isinstance(env.get("prefetched_facts"), dict) else {}
    return sorted(k for k in pf if not _meta_key(k))


def introspect(env: dict) -> dict:
    """Return a uniform view of a compile envelope:

    ``{label, procedure, classifier_confidence, probe_families, probe_empty}``

    - ``label``: artifact type ('l1_probe' / 'facts' / 'full' / 'lean' / 'contract').
    - ``probe_families``: substantive prefetched-fact keys.
    - ``probe_empty``: True when an L1 envelope carries no substantive families
      (the real "miss" signal). Non-L1 labels are never probe_empty (by design
      lower-signal, not misses).
    """
    if not isinstance(env, dict):
        return {"label": "", "procedure": "", "classifier_confidence": None, "probe_families": [], "probe_empty": False}
    summary = env.get("summary") if isinstance(env.get("summary"), dict) else {}
    plan = env.get("plan") if isinstance(env.get("plan"), dict) else {}

    label = summary.get("artifact_type") or (env.get("artifact") if isinstance(env.get("artifact"), str) else "") or ""
    procedure = summary.get("procedure") or plan.get("procedure") or ""
    conf = summary.get("classifier_confidence")
    if conf is None:
        conf = plan.get("classifier_confidence")

    families = probe_families(env)
    probe_empty = (label == "l1_probe") and not families
    return {
        "label": label,
        "procedure": procedure,
        "classifier_confidence": conf,
        "probe_families": families,
        "probe_empty": probe_empty,
    }
