"""B6 prototype — adversarial-domain MCP sampling compression.

Wires the existing ``Context.sample`` round-trip
(:mod:`roam.mcp_extras.sampling`) into the ``roam adversarial`` output
path. The adversarial command already emits structured *challenges*
(graph-derived architectural objections, each carrying a
"defend this choice" ``question`` field). This module re-frames those
challenges for the agent's own model in two modes:

* **defend** — the "Dungeon Master" intent from
  ``dev/ARCHITECTURE-FUTURES.md`` §B6: collapse N structured challenges
  into one sharp adversarial brief that pressures the change author to
  justify the structural choices.
* **digest** — the context-budget intent: prime the generic sampling
  summariser with adversarial-domain ``task`` framing so a 40+ challenge
  envelope compresses to a 1-3 KB triage briefing led by the
  highest-severity defensible risk.

Design notes
------------

* **No re-implemented transport.** Both modes compose with the shipped
  :func:`roam.mcp_extras.sampling.compress_with_sampling`. The
  ``ROAM_AI_ENABLED`` opt-in gate, the SamplingResult text extraction,
  and the budget cap all stay single-sourced there.
* **Default-off, never degrading.** When sampling is disabled / absent /
  fails, every entry point returns the full deterministic envelope
  unchanged. Compression only ever *adds* fields; ``summary.verdict``
  (LAW 6) is preserved verbatim.
* **No import-time side effects.** Pure functions; the ``sampling``
  dependency is imported lazily inside :func:`compress_adversarial` (or
  dependency-injected for tests), so importing this module never drags
  in the FastMCP-adjacent stack.

This module is a PROTOTYPE: it is importable and unit-tested, but it is
NOT yet wired into ``mcp_server.py`` / ``cmd_adversarial.py`` — that is a
deliberate serial follow-up (see
``(internal memo)``).
"""

from __future__ import annotations

from typing import Any

from roam.output._severity import severity_rank

# Closed enum for the compression intent. The serial follow-up surfaces
# this as the MCP ``compress_mode`` parameter.
DEFEND = "defend"
DIGEST = "digest"
_MODES = frozenset({DEFEND, DIGEST})

_DEFAULT_MAX_CHALLENGES = 12


_DEFEND_SYSTEM_PROMPT = (
    "You are an adversarial code reviewer playing 'Dungeon Master' for a "
    "structural change. The author must DEFEND their architectural choices "
    "against graph-derived objections. Be terse, concrete, and skeptical. "
    "Lead with the single objection most likely to block merge. Reference "
    "the exact symbol names, file paths, and cycle/layer/cluster facts given "
    "to you. Do not soften: if a challenge is unanswerable as stated, say so. "
    "Output plain prose or a short numbered list -- no JSON, no markdown "
    "tables."
)


def defend_system_prompt() -> str:
    """Return the adversarial 'Dungeon Master' system prompt.

    Distinct from sampling's neutral 'senior engineer summarising a
    report' prompt -- this one primes the model to pressure-test the
    change rather than describe it.
    """
    return _DEFEND_SYSTEM_PROMPT


def _challenges_of(envelope: Any) -> list[dict]:
    """Pull the ``challenges`` list out of an adversarial envelope.

    Tolerant of shape drift: returns an empty list for anything that
    isn't a dict with a list-valued ``challenges`` key.
    """
    if not isinstance(envelope, dict):
        return []
    raw = envelope.get("challenges")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _severity_key(challenge: dict) -> int:
    # Delegate ORDER to the canonical rank (W564: no inline severity-rank
    # tables). ``severity_rank`` is higher=worse (critical=5 ... info=0;
    # unknown/None collapse to -1), so negate it to sort highest-severity
    # FIRST under ascending ``sorted``. The adversarial 4-tier vocabulary
    # (CRITICAL/HIGH/WARNING/INFO) maps onto the canonical rank unchanged,
    # and unknown tokens (-1 -> +1) still sort last, after info (0).
    return -severity_rank(challenge.get("severity"))


def _sorted_challenges(challenges: list[dict], max_challenges: int) -> list[dict]:
    """Stable highest-severity-first sort, capped to the sampling budget."""
    ordered = sorted(challenges, key=_severity_key)
    if max_challenges and max_challenges > 0:
        return ordered[:max_challenges]
    return ordered


def _verdict_of(envelope: Any) -> str:
    if not isinstance(envelope, dict):
        return ""
    summary = envelope.get("summary")
    if isinstance(summary, dict):
        verdict = summary.get("verdict")
        if isinstance(verdict, str):
            return verdict
    return ""


def build_defend_prompt(
    envelope: Any,
    *,
    task: str = "",
    max_challenges: int = _DEFAULT_MAX_CHALLENGES,
) -> str | None:
    """Assemble a single 'defend-this-change' adversarial user prompt.

    Folds the graph-derived challenges (highest severity first, capped at
    ``max_challenges``) into one coherent brief the agent's own model must
    answer. Returns ``None`` when there are zero challenges -- nothing to
    defend, so the caller skips the sampling round-trip entirely.

    The prompt anchors on concrete nouns (challenge titles, severities,
    file locations, the per-challenge ``question`` fields) so the sampled
    model leads with structural specifics rather than abstract summary.
    """
    challenges = _sorted_challenges(_challenges_of(envelope), max_challenges)
    if not challenges:
        return None

    verdict = _verdict_of(envelope)
    total = len(_challenges_of(envelope))
    shown = len(challenges)

    lines: list[str] = []
    header = (
        f"A roam adversarial review found {total} architectural "
        f"challenge{'s' if total != 1 else ''} on the changed code."
    )
    lines.append(header)
    if verdict:
        lines.append(f"Verdict: {verdict}")
    if task:
        lines.append(f"The change author's stated task: {task!r}.")
    if shown < total:
        lines.append(f"The {shown} highest-severity challenges are listed below.")
    lines.append("")
    lines.append("--- CHALLENGES ---")

    for i, c in enumerate(challenges, 1):
        sev = str(c.get("severity", "?")).upper()
        title = str(c.get("title") or c.get("type") or "?")
        lines.append(f"{i}. [{sev}] {title}")
        desc = str(c.get("description") or "").strip()
        if desc:
            lines.append(f"   {desc}")
        location = str(c.get("location") or "").strip()
        if location:
            lines.append(f"   Location: {location}")
        question = str(c.get("question") or "").strip()
        if question:
            lines.append(f'   Defend: "{question}"')
    lines.append("--- END CHALLENGES ---")
    lines.append("")
    lines.append(
        "As the author, defend these structural choices OR concede where a "
        "challenge cannot be answered. Lead with the objection most likely "
        "to block merge. Be concrete: name the symbols, files, and cycles "
        "above."
    )
    return "\n".join(lines)


def digest_task_hint(envelope: Any, *, base_task: str = "") -> str:
    """Derive an adversarial-domain ``task`` for digest-mode compression.

    Passed straight through to ``compress_with_sampling(..., task=...)`` so
    the generic summariser leads with the highest-severity defensible risk
    instead of producing neutral prose. Prepends the caller's ``base_task``
    (the MCP session hint) when present.
    """
    challenges = _challenges_of(envelope)
    base = base_task.strip()
    if not challenges:
        hint = "triage an adversarial architecture review that found no challenges"
    else:
        hint = (
            f"triage {len(challenges)} adversarial architecture challenges on "
            "changed code; lead with the highest-severity structural risks the "
            "author must defend"
        )
    if base:
        return f"{base}; {hint}"
    return hint


def apply_defend_briefing(envelope: dict, briefing_text: str) -> dict:
    """Merge a sampled defend-briefing into the adversarial envelope.

    Adds a top-level ``defend_briefing`` field + a
    ``summary.defend_briefing_tokens`` estimate and stamps
    ``summary.compressed = True``. The original ``summary.verdict`` is
    preserved (LAW 6 -- clients depend on it) and the input envelope is
    never mutated. A blank briefing is a no-op returning the input.
    """
    if not isinstance(envelope, dict):
        return envelope
    text = (briefing_text or "").strip()
    if not text:
        return envelope

    out = dict(envelope)
    summary = dict(out.get("summary") or {})
    summary["compressed"] = True
    # Rough estimate; clients can ignore. Mirrors sampling.py's heuristic.
    summary["defend_briefing_tokens"] = max(1, len(text) // 4)
    out["summary"] = summary
    out["defend_briefing"] = text
    return out


async def compress_adversarial(
    ctx: Any,
    envelope: Any,
    *,
    mode: str = DIGEST,
    task: str = "",
    summarize: bool | None = None,
    max_tokens: int = 600,
    max_challenges: int = _DEFAULT_MAX_CHALLENGES,
    sampling: Any = None,
) -> Any:
    """Optionally compress an adversarial envelope via MCP sampling.

    The single async entry point the serial follow-up wires into the
    ``roam_adversarial`` MCP wrapper. Dispatches on ``mode``:

    * ``"digest"`` -- compress the full envelope into a triage briefing
      (adversarial-domain ``task`` priming via :func:`digest_task_hint`).
    * ``"defend"`` -- build a 'defend-this-change' prompt via
      :func:`build_defend_prompt` and sample it under the adversarial
      system prompt, merging the result under ``defend_briefing``.

    Returns the envelope UNCHANGED when any of: ``mode`` is unknown,
    ``ctx`` is ``None``, ``envelope`` isn't a dict, sampling is disabled /
    unavailable / fails, or (defend mode) there are no challenges. The
    ``ROAM_AI_ENABLED`` opt-in gate is enforced by the delegated
    ``compress_with_sampling`` -- this function does not re-check it.

    ``sampling`` is dependency-injected for tests; in production it
    defaults to the real :mod:`roam.mcp_extras.sampling` module (imported
    lazily so importing this module stays side-effect-free).

    ``summarize`` mirrors the ``roam_understand`` parameter: ``True`` /
    ``False`` force on/off; ``None`` defers to the delegated gate. An
    explicit ``False`` short-circuits before any sampling call.
    """
    if summarize is False:
        return envelope
    if mode not in _MODES:
        return envelope
    if ctx is None or not isinstance(envelope, dict):
        return envelope

    if sampling is None:
        from roam.mcp_extras import sampling as _sampling

        sampling = _sampling

    if mode == DEFEND:
        prompt = build_defend_prompt(envelope, task=task, max_challenges=max_challenges)
        if prompt is None:
            # No challenges -> nothing to defend. Return deterministic envelope.
            return envelope
        compressed = await sampling.compress_with_sampling(
            ctx,
            prompt,
            task=task,
            target="adversarial-defense",
            max_tokens=max_tokens,
            system_prompt=defend_system_prompt(),
        )
        if not compressed:
            return envelope
        briefing = compressed.get("summary", "") if isinstance(compressed, dict) else ""
        return apply_defend_briefing(envelope, briefing)

    # DIGEST mode -- compress the whole envelope with adversarial priming.
    compressed = await sampling.compress_with_sampling(
        ctx,
        envelope,
        task=digest_task_hint(envelope, base_task=task),
        target="adversarial-review",
        max_tokens=max_tokens,
    )
    return sampling.maybe_apply_compression(envelope, compressed)
