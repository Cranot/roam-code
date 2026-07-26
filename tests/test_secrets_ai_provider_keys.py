"""The secret scanner must detect the credentials roam's own users handle.

roam's audience is AI-agent developers, so Anthropic/OpenAI/xAI/Groq keys are the
credentials most likely to sit in their repos, ``.env`` files, notebooks and pasted
logs. Until 2026-07-27 the catalogue had **none** of them -- it detected AWS,
GitHub, Stripe, Slack and a dozen others, while passing an AI provider key as
clean. That is false assurance exactly where the audience is most exposed.

Found by pointing this scanner at a real transcript corpus: it reported a live
``sk-ant-oat01-`` OAuth token (1-year validity) as clean.

METHOD NOTE -- why one case PER FAMILY:
the first gate built against this defect self-tested with a planted *GitHub*
token, passed, and was declared working, while the Anthropic token it was meant
to catch sailed through. A self-test that exercises a pattern already known to be
covered proves nothing about coverage. So every provider family gets its own
planted case, and a benign string must stay clean so the patterns cannot be made
to "pass" by matching everything.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_secrets import _COMPILED_PATTERNS


def _detect(text: str) -> set[str]:
    """Pattern names that fire on ``text``."""
    return {pat["name"] for pat in _COMPILED_PATTERNS if pat["regex"].search(text)}


# (planted secret, expected pattern name). Values are synthetic, never real.
AI_PROVIDER_CASES = [
    ("sk-ant-oat01-" + "Aa9_-" * 12, "Anthropic OAuth Token"),
    ("sk-ant-api03-" + "Bb8_-" * 12, "Anthropic API Key"),
    ("sk-proj-" + "Cc7" * 12, "OpenAI Project Key"),
    ("sk-" + "D" * 48, "OpenAI API Key"),
    ("xai-" + "E" * 24, "xAI API Key"),
    ("gsk_" + "F" * 24, "Groq API Key"),
    ("hf_" + "G" * 34, "HuggingFace Token"),
    ("r8_" + "H" * 37, "Replicate API Token"),
    ("AIza" + "I" * 35, "Google API Key"),  # pre-existing; covers Gemini
]


@pytest.mark.parametrize(("secret", "expected"), AI_PROVIDER_CASES)
def test_ai_provider_key_is_detected(secret: str, expected: str) -> None:
    """Each provider family is detected by its own named pattern."""
    hits = _detect(f"API_KEY = '{secret}'")
    assert hits, f"{expected}: no pattern fired -- this credential would leak"
    assert expected in hits, f"{expected}: expected that pattern, got {sorted(hits)}"


BENIGN = [
    "lets refactor the parser and add a test",
    "why cant it just reuse the existing index instead of rebuilding",
    "the sk- prefix is discussed in our docs but this is not a key",
    "def add(a, b):\n    return a + b",
    "https://example.com/some/path?q=search",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_is_not_flagged(text: str) -> None:
    """Patterns must not fire on ordinary prose or code.

    Without this, a catalogue could 'pass' every detection test by matching
    everything, which would make the scanner useless in the opposite direction.
    """
    assert not _detect(text), f"false positive on: {text!r}"


def test_catalogue_covers_the_major_ai_providers() -> None:
    """Guards the gap itself, not just today's patterns.

    The defect was a whole CATEGORY missing, so assert the category is present.
    A future refactor that drops these entries fails here even if every
    individual case above were deleted alongside them.
    """
    names = {pat["name"] for pat in _COMPILED_PATTERNS}
    required = {
        "Anthropic OAuth Token",
        "Anthropic API Key",
        "OpenAI Project Key",
        "OpenAI API Key",
        "xAI API Key",
        "Groq API Key",
    }
    missing = required - names
    assert not missing, f"AI-provider patterns missing from the catalogue: {sorted(missing)}"
