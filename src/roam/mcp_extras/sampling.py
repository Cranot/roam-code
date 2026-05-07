"""Sampling-driven result compression.

When a tool produces a large structured result (full ``health``,
``understand``, ``repo-map`` envelopes are 50-200 KB) the agent
context fills up fast. With MCP sampling the *server* can call back
into the *client's* LLM mid-tool, ask it to summarise the payload for
the agent's current task, and return a 1-3 KB briefing instead.

Why this is the right place
---------------------------

* roam's positioning is "100% local, zero API keys". Sampling
  preserves that because the LLM doing the compression is the
  agent's own model -- no extra credentials.
* Clients without sampling support fall through silently.
* The original payload is preserved under a ``raw`` key when
  ``include_raw`` is set, so callers can diff briefings against the
  source data when debugging.

API
---

The single entry point is :func:`compress_with_sampling`. It is
``async``, takes a payload + a task description, and returns a
compressed dict ready to merge back into the tool's envelope.
"""

from __future__ import annotations

import json
from typing import Any

# A small cap so we don't blow out the sampling budget by accident.
_MAX_PAYLOAD_CHARS = 60_000
_DEFAULT_MAX_TOKENS = 600


_BRIEFING_SYSTEM_PROMPT = (
    "You are a senior engineer summarising a structured codebase report "
    "for a teammate. Be terse, high-signal, and concrete. Focus only on "
    "what is decision-relevant for the stated task. No filler, no "
    "headings unless the report has multiple sections worth separating. "
    "Output plain prose or short lists -- no JSON, no markdown tables."
)


def _shrink_payload(payload: Any) -> str:
    """Coerce payload to a string that fits the sampling budget."""
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, indent=2, default=str, sort_keys=False)
        except (TypeError, ValueError):
            text = str(payload)
    if len(text) > _MAX_PAYLOAD_CHARS:
        text = text[:_MAX_PAYLOAD_CHARS] + "\n... [truncated for sampling budget]"
    return text


def _build_user_prompt(payload_text: str, task: str, target: str) -> str:
    target_hint = f" The user is focused on `{target}`." if target else ""
    task_hint = f" Their stated task is: {task!r}." if task else ""
    return (
        f"Summarise the following roam-code report.{target_hint}{task_hint}\n\n"
        f"Goal: in <= 200 words, give the engineer the verdict, the 3-5 "
        f"most important specifics (file paths, symbol names, scores), "
        f"and the single most useful next step. If the report says "
        f"everything is fine, say so in one line.\n\n"
        f"--- REPORT ---\n{payload_text}\n--- END REPORT ---"
    )


async def compress_with_sampling(
    ctx: Any,
    payload: Any,
    *,
    task: str = "",
    target: str = "",
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    include_raw: bool = False,
) -> dict[str, Any] | None:
    """Ask the client's LLM for a short briefing on ``payload``.

    Returns ``None`` if sampling is unavailable (no Context, no
    ``ctx.sample`` method, transport failure). Callers should treat
    ``None`` as "fall back to the raw payload".

    sending payloads through the client's LLM is OFF by
    default for GDPR / EU AI Act credibility. Set
    ``ROAM_AI_ENABLED=1`` (or ``=true``) to opt in. Without the env
    var, this function returns ``None`` even if the client offers
    sampling — the caller falls back to the raw envelope.

    On success returns a dict::

        {
            "compressed": True,
            "summary": "<sampled briefing text>",
            "tokens_estimated": <int>,
            "task": <str>,
            "target": <str>,
            "raw": <original payload>  # only when include_raw=True
        }
    """
    import os as _os

    if _os.environ.get("ROAM_AI_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    if ctx is None:
        return None
    sampler = getattr(ctx, "sample", None)
    if not callable(sampler):
        return None

    payload_text = _shrink_payload(payload)
    user_prompt = _build_user_prompt(payload_text, task, target)

    try:
        result = await sampler(
            user_prompt,
            system_prompt=_BRIEFING_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except Exception:
        return None

    summary_text = _extract_summary_text(result)
    if not summary_text:
        return None

    out: dict[str, Any] = {
        "compressed": True,
        "summary": summary_text.strip(),
        # rough estimate; clients can ignore.
        "tokens_estimated": max(1, len(summary_text) // 4),
        "task": task,
        "target": target,
    }
    if include_raw:
        out["raw"] = payload
    return out


def _extract_summary_text(result: Any) -> str:
    """Robustly pull the text content from a SamplingResult."""
    if result is None:
        return ""
    # FastMCP returns a SamplingResult with a `.text` shortcut on most builds.
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    # Fall back to digging into content blocks.
    content = getattr(result, "content", None)
    if content is not None:
        if isinstance(content, list):
            chunks = []
            for block in content:
                t = getattr(block, "text", None)
                if isinstance(t, str):
                    chunks.append(t)
            if chunks:
                return "\n".join(chunks)
        text = getattr(content, "text", None)
        if isinstance(text, str):
            return text
    if isinstance(result, str):
        return result
    return ""


def maybe_apply_compression(
    original: dict[str, Any],
    compressed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge a sampling result back into a tool envelope.

    If sampling failed, the original is returned unchanged. If it
    succeeded, the original ``summary.verdict`` is preserved (clients
    rely on it) but a new ``briefing`` field is added at the top
    level, and ``summary.compressed`` is set to ``True``.
    """
    if not compressed:
        return original

    out = dict(original)
    summary = dict(out.get("summary") or {})
    summary["compressed"] = True
    summary["briefing_tokens"] = compressed.get("tokens_estimated")
    out["summary"] = summary
    out["briefing"] = compressed["summary"]
    return out
