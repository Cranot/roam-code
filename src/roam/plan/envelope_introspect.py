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

``*_unavailable`` keys — two lenses, not a bug. ``_meta_key`` treats them as
annotation/meta here (the *diagnostic* lens: substantive agent-actionable
data), while ``_L1_PROCEDURE_METADATA`` in ``compiler.py`` counts the paired
``*_unavailable`` key toward ``l1_probe`` promotion (the *routing* lens: the
probe fired and emitted a structured honest-degradation result). See
``_meta_key`` for the full distinction and the consequence (an L1 envelope
can be both ``l1_probe`` AND ``probe_empty``).
"""

from __future__ import annotations


def _meta_key(k: str) -> bool:
    """True for annotation keys that are NOT real probe families.

    Two suffix families: ``*_definition`` (a metric's vocabulary sidecar,
    Pattern 3) and ``*_unavailable`` (an honest-degradation remediation,
    e.g. ``symbol_definitions_unavailable`` = "run `roam search`"). Neither
    carries substantive agent-actionable data, so both are excluded from
    :func:`probe_families`; an L1 envelope whose ONLY prefetched facts are
    such keys is reported ``probe_empty``.

    Deliberate distinction from ``_L1_PROCEDURE_METADATA`` (compiler.py):
    the two answer DIFFERENT questions, so an ``*_unavailable`` key is meta
    here and signal there — not a contradiction.

    * compiler (routing lens): "did the probe FIRE and emit a structured
      result, even an honest-degradation one?" A paired ``*_unavailable``
      key COUNTS toward ``l1_probe`` promotion. Nearly every
      task-text-driven procedure lists its data key beside an
      ``*_unavailable`` partner (``symbol_defined_where`` ->
      ``symbol_definitions`` + ``symbol_definitions_unavailable``; likewise
      ``top_n_ranking``, ``compare_x_vs_y``, ``file_history``,
      ``repo_structure``, ``entry_point_where``, ``config_where``,
      ``session_meta``, ``freeform_explore``) so a correct-but-empty probe
      does not silently degrade to ``full``.
    * here (diagnostic lens): "does the envelope carry SUBSTANTIVE facts a
      downstream agent can act on?" The same ``*_unavailable`` key does NOT
      count -- only the remediation string is present.

    Consequence: an L1 envelope degraded to unavailable-only content is
    correctly BOTH ``l1_probe`` (probe fired; routing was right) AND
    ``probe_empty`` (no substantive facts survived) -- a distinct signal
    ("probe ran but the source was empty"), not a routing miss. Making
    degraded probes count as non-empty in this lens is a BEHAVIOR CHANGE:
    it re-pins ``test_l1_with_only_annotation_keys_is_empty`` and changes
    ``top_misses`` in ``compiler-corpus``. Raise it as a separate issue;
    do not bury it here.
    """
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
      (the real "miss" signal — *no substantive facts to act on*). A
      correctly-routed L1 envelope that degraded to ``*_unavailable``-only
      content is BOTH ``l1_probe`` AND ``probe_empty`` (probe fired, source
      was empty); that is a distinct signal from a routing miss, not a
      contradiction. See ``_meta_key``. Non-L1 labels are never probe_empty
      (by design lower-signal, not misses).
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
