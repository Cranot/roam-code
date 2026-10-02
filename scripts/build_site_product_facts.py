#!/usr/bin/env python3
"""Generate the site's product-facts view and explicitly bound static copy.

Registry counts and source version retain their existing owners. Offer policy
is editorial, not measured; change it only with adopted terms. No runtime fetch.
Default mode checks without writing. Refuse unknown or malformed bindings before
writing any output. Historical measurements and install pins are not current facts.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dev.build_readme_counts import collect_counts  # noqa: E402

SITE = Path("templates/distribution/landing-page")
POLICY = Path("dev/site-product-policy.json")
SNAPSHOT = SITE / "data/product-facts.json"
STATES = {"AVAILABLE", "BETA", "PLANNED", "RESEARCH"}
FACT = re.compile(r"<!-- product-fact:([\w.]+) -->(.*?)<!-- /product-fact -->", re.S)
BLOCK = re.compile(r"<!-- product-block:([\w-]+) -->(.*?)<!-- /product-block -->", re.S)
REQUIRED = {
    "audit.html": {"teamReplayPrice", "teamReplayPrCount", "deepReplayPrice", "deepReplayPrCount"},
    "pricing.html": {
        "teamReplayPrice",
        "teamReplayPrCount",
        "deepReplayPrice",
        "deepReplayPrCount",
        "productAvailability.review",
        "productAvailability.cloud",
        "reviewTeamProposedMonthlyPrice",
    },
    "press.html": {
        "teamReplayPrice",
        "teamReplayPrCount",
        "deepReplayPrice",
        "deepReplayPrCount",
        "sourceVersion",
        "reviewTeamProposedMonthlyPrice",
    },
    "compare.html": {"teamReplayPrice", "teamReplayPrCount", "deepReplayPrice", "deepReplayPrCount"},
    "llms.txt": {"teamReplayPrice", "deepReplayPrice"},
    "docs/architecture.html": {"teamReplayPrCount", "deepReplayPrCount"},
    # Technical reference links to the offer rather than repeating paid scope.
    # Keep the page mandatory and scan copied claims without requiring a pitch.
    "docs/mcp-usage.html": set(),
    "index.html": {"defaultMcpToolCount"},
    "status.html": {"sourceVersion", "productAvailability.review", "productAvailability.cloud"},
    "examples/team-replay-report.md": {"teamReplayPrice", "teamReplayPrCount", "teamCreditPrice"},
}


def validate_offer_terms(root: Path, facts: dict) -> None:
    """Written templates remain adopted terms, never auto-rewritten marketing."""
    text = (root / "templates/legal/sow-pr-replay.md").read_text(encoding="utf-8")
    for tier in ("team", "deep"):
        match = re.search(
            rf"\*\*{tier.title()}\*\* — (\d+) [^\n]*(?:\n(?!- \[ \])[^\n]*)*?USD \$([\d,]+) \(\$([\d,.]+) credits", text
        )
        if not match or (int(match[1]), int(match[2].replace(",", "")), float(match[3].replace(",", ""))) != (
            facts[f"{tier}ReplayPrCount"],
            facts[f"{tier}ReplayPrice"],
            facts[f"{tier}CreditPrice"],
        ):
            raise ValueError(
                f"{tier} policy disagrees with adopted SOW scope/price/credit; review terms before generating"
            )


def product_facts(root: Path = ROOT) -> dict:
    policy = json.loads((root / POLICY).read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != 1:
        raise ValueError("Unsupported site product policy schema")
    expected = {"cli", "mcp", "prReplay", "review", "cloud"}
    availability = policy.get("productAvailability", {})
    if (
        not isinstance(availability, dict)
        or set(availability) != expected
        or any(not isinstance(state, str) or state not in STATES for state in availability.values())
    ):
        raise ValueError("Invalid or missing product availability")
    for key in (
        "teamReplayPrice",
        "teamReplayPrCount",
        "deepReplayPrice",
        "deepReplayPrCount",
        "reviewTeamProposedMonthlyPrice",
    ):
        if type(policy.get(key)) is not int or policy[key] <= 0:
            raise ValueError(f"Invalid positive integer: {key}")
    if policy.get("license") != "Apache-2.0" or policy.get("currency") != "USD":
        raise ValueError("License/currency changes require a renderer and adopted-terms review")
    if policy.get("sampleCommitRange") != "HEAD~5..HEAD":
        raise ValueError("Sample range must agree with cmd_pr_replay.py; review its prerequisites too")
    if policy.get("reviewCreditPercent") != 50:
        raise ValueError("Credit-policy changes need adopted terms and a full claim review")
    if availability != {
        "cli": "AVAILABLE",
        "mcp": "AVAILABLE",
        "prReplay": "AVAILABLE",
        "review": "PLANNED",
        "cloud": "PLANNED",
    }:
        raise ValueError(
            "Availability transition requires updating the linked product descriptions and launch evidence"
        )
    c = collect_counts(root)
    if not isinstance(c.pyproject_version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)", c.pyproject_version
    ):
        raise ValueError("Missing or invalid source version")
    counts = {
        "cliCommandCount": c.command_names,
        "mcpToolCount": c.mcp_full,
        "defaultMcpToolCount": c.mcp_default_preset,
        "languageCount": c.languages,
    }
    if any(type(value) is not int or value <= 0 for value in counts.values()):
        raise ValueError("Refusing empty product inventory")
    derived = {"reviewAnnualPrice": policy["reviewTeamProposedMonthlyPrice"] * 12}
    for tier in ("team", "deep"):
        fee = policy[f"{tier}ReplayPrice"]
        derived[f"{tier}CreditPrice"] = fee * policy["reviewCreditPercent"] / 100
        derived[f"{tier}RemainingPrice"] = max(0, derived["reviewAnnualPrice"] - derived[f"{tier}CreditPrice"])
        derived[f"{tier}CombinedPrice"] = fee + derived[f"{tier}RemainingPrice"]
    return {
        **policy,
        "sourceVersion": c.pyproject_version,
        **counts,
        **derived,
        "provenance": {
            "counts": "src/roam/surface_counts.py and src/roam/languages/registry.py",
            "sourceVersion": "pyproject.toml (source identity, not a fresh PyPI observation)",
            "policy": POLICY.as_posix(),
        },
    }


def fact_value(facts: dict, key: str) -> str:
    value = facts
    for part in key.split("."):
        value = value[part]
    if key.endswith("Price"):
        return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"
    if key.startswith("productAvailability."):
        return value.title()
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"Not a scalar product fact: {key}")
    return str(value)


def render(text: str, facts: dict) -> str:
    if text.count("<!-- product-fact:") != len(FACT.findall(text)) or text.count("<!-- /product-fact -->") != len(
        FACT.findall(text)
    ):
        raise ValueError("Malformed product fact markers")
    text = FACT.sub(
        lambda m: f"<!-- product-fact:{m[1]} -->{html.escape(fact_value(facts, m[1]))}<!-- /product-fact -->", text
    )

    def block(match: re.Match) -> str:
        if match[1] != "snapshot":
            raise ValueError(f"Unknown product block: {match[1]}")
        states = "; ".join(f"{key}: {value}" for key, value in facts["productAvailability"].items())
        body = (
            f"Source version: {facts['sourceVersion']}; {facts['cliCommandCount']} CLI commands; "
            f"{facts['mcpToolCount']} MCP tools; {facts['defaultMcpToolCount']} default core tools; "
            f"{facts['languageCount']} languages; {facts['license']}.\n"
            f"Product states: {states}.\n"
            f"{facts['description']}\n"
            "Source snapshot, not a fresh installed-package check. Paid reports require agreed written scope.\n"
            "Machine-readable facts: https://roam-code.com/data/product-facts.json"
        )
        return f"<!-- product-block:snapshot -->\n{body}\n<!-- /product-block -->"

    if text.count("<!-- product-block:") != len(BLOCK.findall(text)) or text.count("<!-- /product-block -->") != len(
        BLOCK.findall(text)
    ):
        raise ValueError("Malformed product block markers")
    text = BLOCK.sub(block, text)
    # JSON-LD is regenerated only in the explicitly labelled service record.
    pattern = re.compile(r'(<script type="application/ld\+json" data-product-offers>)(.*?)(</script>)', re.S)

    def offers(match: re.Match) -> str:
        data = json.loads(match[2])
        rows = data["offers"]
        if len(rows) != 3 or [row["name"] for row in rows] != [
            "PR Replay — Sample (DIY)",
            "PR Replay — Team",
            "PR Replay — Deep",
        ]:
            raise ValueError("Unexpected PR Replay offer shape")
        for row, tier in zip(rows[1:], ("team", "deep")):
            row["price"] = str(facts[f"{tier}ReplayPrice"])
            row["priceCurrency"] = facts["currency"]
            row["description"], count = re.subn(
                r"^\d+-PR report", f"{facts[f'{tier}ReplayPrCount']}-PR report", row["description"]
            )
            if count != 1:
                raise ValueError("Missing PR scope in offer")
            # A commissioned service is not an in-stock SKU or a future preorder.
            row.pop("availability", None)
        return match[1] + "\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n  " + match[3]

    text = pattern.sub(offers, text)
    return text


def run(root: Path = ROOT, *, write: bool = False) -> int:
    facts = product_facts(root)
    validate_offer_terms(root, facts)
    pending = {root / SNAPSHOT: json.dumps(facts, ensure_ascii=False, indent=2) + "\n"}
    scanned = 0
    for path in sorted((root / SITE).rglob("*")):
        if path.suffix not in {".html", ".txt", ".md"}:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root / SITE).as_posix()
        keys = {match[0] for match in FACT.findall(text)}
        if REQUIRED.get(relative, set()) - keys:
            raise ValueError(f"{relative}: missing required fact bindings")
        if relative == "llms.txt" and "<!-- product-block:snapshot -->" not in text:
            raise ValueError("llms.txt: missing generated snapshot")
        if relative == "audit.html" and text.count("data-product-offers>") != 1:
            raise ValueError("audit.html: missing or ambiguous service offer binding")
        if relative in REQUIRED:
            # Catch newly copied obvious offer claims as well as broken markers.
            # History and unrelated proposed subscription prices are not rewritten.
            unbound = FACT.sub("", text)
            unbound = re.sub(r"<script\b.*?</script>|<!--.*?-->", "", unbound, flags=re.S)
            unbound = re.sub(r"<[^>]+>", " ", unbound)
            current_lines = "\n".join(
                line for line in unbound.splitlines() if not re.search(r"planned|proposed|/mo", line, re.I)
            )
            if re.search(
                r"\b(?:Team|Deep)[ \t]+(?:covers[ \t]+\d+[ \t]+PRs|\(?\$\d)|\$[\d,]+[ \t]+(?:Team|Deep)\b",
                current_lines,
            ):
                raise ValueError(f"{relative}: unbound offer price/scope; add a product-fact binding")
        pending[path] = render(text, facts)
    if not scanned or not all((root / SITE / name).is_file() for name in REQUIRED):
        raise ValueError("Refusing missing or empty required site scope")
    changed = [path for path, data in pending.items() if not path.exists() or path.read_text(encoding="utf-8") != data]
    for path in changed:
        print(f"{'WRITE' if write else 'DRIFT'} {path.relative_to(root).as_posix()}")
    if write:
        for path in changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(pending[path], encoding="utf-8")
    print(f"Product facts: {scanned} content files inspected; {len(changed)} changed; historical records retained")
    return 0 if write or not changed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        return run(write=args.write)
    except (KeyError, ValueError, OSError) as exc:
        print(f"Product facts unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
