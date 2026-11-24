#!/usr/bin/env python3
"""Generate the site's product-facts view and explicitly bound static copy.

Registry counts and source version retain their existing owners. Offer policy
is editorial, not measured; change it only with adopted terms. No runtime fetch.
Default mode checks without writing. Refuse unknown or malformed bindings before
writing any output. Historical measurements and install pins are not current facts.
PR Replay tier states (policy ``replayTiers``) select stored offer copy from
dev/site-replay-offers; refuse any served file that presents a paused tier.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dev.build_readme_counts import collect_counts  # noqa: E402

SITE = Path("templates/distribution/landing-page")
POLICY = Path("dev/site-product-policy.json")
SNAPSHOT = SITE / "data/product-facts.json"
# Stored copy for each PR Replay tier state. Served files carry only the
# variant their tier state selects, so reopening a tier is a policy change
# followed by --write, not a hand restoration of every page.
OFFERS = Path("dev/site-replay-offers")
README = Path("README.md")
STATES = {"AVAILABLE", "BETA", "PLANNED", "RESEARCH"}
TIER_STATES = {"OPEN", "PAUSED"}
REPLAY_TIERS = ("sample", "team", "deep")
PAID_TIERS = ("team", "deep")
FACT = re.compile(r"<!-- product-fact:([\w.]+) -->(.*?)<!-- /product-fact -->", re.S)
BLOCK = re.compile(r"<!-- product-block:([\w-]+) -->(.*?)<!-- /product-block -->", re.S)
# gate: a paid tier (team, deep), "paid" (open while any paid tier is open) or
# "paused" (its paused variant shows while any paid tier is paused).
OFFER = re.compile(r"<!-- replay-offer:(team|deep|paid|paused):([\w-]+) -->(.*?)<!-- /replay-offer -->", re.S)
VARIANT = re.compile(r"<!-- variant:([\w-]+):(open|paused) -->(.*?)<!-- /variant -->", re.S)
PAUSED_TIERS_SLOT = "[[paused-tiers]]"
# Pages that govern orders already agreed, and dated history. They keep their
# terms while a tier is paused; every other served file is checked.
EXISTING_ORDER_PAGES = {"terms.html", "refund.html", "privacy.html", "thank-you.html", "changelog.html"}
PAID_CTA = re.compile(
    r"request (?:a |the )?(?:written |paid |scoped )?(?:PR Replay )?report"
    r"|ask about a (?:paid )?report|paid report options|compare the paid reports|explore the paid report"
    r"|commission(?:ing)? a (?:paid )?(?:PR Replay )?(?:report|engagement)"
    r"|paid (?:PR Replay )?(?:reports?|audit)(?: (?:by request|by email|can be requested|add|include|offer|cover)|:)"
    r"|paid report by request|paid interpretation|before purchase",
    re.I,
)
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
    # Architecture links to report availability rather than repeating paid scope.
    "docs/architecture.html": set(),
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
    tiers = policy.get("replayTiers")
    if (
        not isinstance(tiers, dict)
        or tuple(tiers) != REPLAY_TIERS
        or any(not isinstance(state, str) or state not in TIER_STATES for state in tiers.values())
    ):
        raise ValueError("Invalid or missing PR Replay tier states (sample, team, deep: OPEN or PAUSED)")
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


def tier_open(facts: dict, tier: str) -> bool:
    return facts["replayTiers"][tier] == "OPEN"


def paused_paid_tiers(facts: dict) -> list[str]:
    return [tier for tier in PAID_TIERS if not tier_open(facts, tier)]


def tier_titles(tiers: list[str]) -> str:
    titles = [tier.title() for tier in tiers]
    return " and ".join(titles) if len(titles) < 3 else ", ".join(titles[:-1]) + " and " + titles[-1]


def gate_state(facts: dict, gate: str) -> str:
    if gate in PAID_TIERS:
        return "open" if tier_open(facts, gate) else "paused"
    if gate == "paid":
        return "open" if any(tier_open(facts, tier) for tier in PAID_TIERS) else "paused"
    if gate == "paused":
        return "paused" if paused_paid_tiers(facts) else "open"
    raise ValueError(f"Unknown replay offer gate: {gate}")


def fact_tier(key: str) -> str | None:
    """The paid tier a bound fact describes; its binding is required only while that tier is open."""
    return next((tier for tier in PAID_TIERS if key.startswith(tier)), None)


def parse_variants(text: str, source: str) -> dict:
    """Stored variants: name -> {"open"|"paused": body}. Anything else in the store is refused."""
    variants: dict = {}
    for name, state, body in VARIANT.findall(text):
        if state in variants.setdefault(name, {}):
            raise ValueError(f"{source}: duplicate stored variant {name}:{state}")
        variants[name][state] = body
    leftover = re.sub(r"<!--.*?-->", "", VARIANT.sub("", text), flags=re.S)
    if leftover.strip() or text.count("<!-- variant:") != len(VARIANT.findall(text)):
        raise ValueError(f"{source}: malformed stored offer variants")
    return variants


def markdown_marker_findings(text: str) -> list[str]:
    """CommonMark opens a raw HTML block on a line that starts with ``<!--``.

    A replay-offer marker at the start of a Markdown line must therefore stand
    alone between blank lines; otherwise the Markdown beside it renders as raw
    text or the surrounding block structure changes.
    """
    lines = text.split("\n")
    findings = []
    for index, line in enumerate(lines):
        if not re.match(r" {0,3}<!-- /?replay-offer", line):
            continue
        if re.sub(r"<!--.*?-->", "", line).strip():
            findings.append(f"line {index + 1}: Markdown shares a line with a block-level replay offer marker")
        for near in (index - 1, index + 1):
            if 0 <= near < len(lines) and lines[near].strip():
                findings.append(f"line {index + 1}: block-level replay offer marker needs blank lines around it")
    return findings


def render(
    text: str, facts: dict, variants: dict | None = None, offers: dict | None = None, *, markdown: bool = False
) -> str:
    if text.count("<!-- replay-offer:") != len(OFFER.findall(text)) or text.count("<!-- /replay-offer -->") != len(
        OFFER.findall(text)
    ):
        raise ValueError("Malformed replay offer markers")

    def offer(match: re.Match) -> str:
        gate, name = match[1], match[2]
        if variants is None or name not in variants:
            raise ValueError(f"No stored variants for replay offer: {name}")
        body = variants[name].get(gate_state(facts, gate), "")
        body = body.replace(PAUSED_TIERS_SLOT, tier_titles(paused_paid_tiers(facts)))
        if markdown and (match.start() == 0 or text[match.start() - 1] == "\n"):
            # A Markdown block variant is whole blocks: each marker on its own line between blank lines.
            body = "\n\n" + body.strip("\n") + "\n\n" if body.strip() else "\n\n"
        return f"<!-- replay-offer:{gate}:{name} -->{body}<!-- /replay-offer -->"

    text = OFFER.sub(offer, text)
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
        tiers = "; ".join(f"{key}: {value}" for key, value in facts["replayTiers"].items())
        paused = paused_paid_tiers(facts)
        terms = "Paid reports require agreed written scope."
        if paused:
            terms = (
                f"Paid {tier_titles(paused)} reports are paused for new requests; existing orders keep their agreed terms."
                + ("" if len(paused) == len(PAID_TIERS) else " " + terms)
            )
        body = (
            f"Source version: {facts['sourceVersion']}; {facts['cliCommandCount']} CLI commands; "
            f"{facts['mcpToolCount']} MCP tools; {facts['defaultMcpToolCount']} default core tools; "
            f"{facts['languageCount']} languages; {facts['license']}.\n"
            f"Product states: {states}.\n"
            f"PR Replay tiers: {tiers}.\n"
            f"{facts['description']}\n"
            f"Source snapshot, not a fresh installed-package check. {terms}\n"
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

    def service(match: re.Match) -> str:
        if offers is None:
            raise ValueError("No stored PR Replay offers for the service record")
        data = json.loads(match[2])
        rows = offers["offers"]
        if [row.get("tier") for row in rows] != list(REPLAY_TIERS):
            raise ValueError("Unexpected PR Replay offer shape")
        data["description"] = offers["serviceDescription"][gate_state(facts, "paid")]
        data["offers"] = []
        # Structured offers list only tiers that take requests now.
        for stored in rows:
            tier = stored["tier"]
            if not tier_open(facts, tier):
                continue
            row = {key: value for key, value in stored.items() if key != "tier"}
            if tier in PAID_TIERS:
                row["price"] = str(facts[f"{tier}ReplayPrice"])
                row["priceCurrency"] = facts["currency"]
                row["description"], count = re.subn(
                    r"^\d+-PR report", f"{facts[f'{tier}ReplayPrCount']}-PR report", row["description"]
                )
                if count != 1:
                    raise ValueError("Missing PR scope in offer")
                # A commissioned service is not an in-stock SKU or a future preorder.
                row.pop("availability", None)
            data["offers"].append(row)
        return match[1] + "\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n  " + match[3]

    text = pattern.sub(service, text)
    return text


def paused_offer_findings(relative: str, text: str, facts: dict) -> list[str]:
    """Known shapes of a paused tier presented as available; semantic review is still required.

    Pages that govern existing orders and dated history are left to their owners.
    """
    if relative in EXISTING_ORDER_PAGES:
        return []
    paused = paused_paid_tiers(facts)
    findings = []
    for script in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S):
        for name in re.findall(r'"name":\s*"([^"]*)"', script):
            if '"Offer"' in script and any(re.search(rf"\b{tier}\b", name, re.I) for tier in paused):
                findings.append(f"structured offer for a paused tier: {name}")
    for link in re.findall(r'href="(mailto:[^"]*)"', text):
        subject = parse_qs(urlparse(html.unescape(link)).query).get("subject", [""])[0]
        if any(re.search(rf"\b{tier}\b", subject, re.I) for tier in paused):
            findings.append(f"request email for a paused tier: {subject}")
    for key, _ in FACT.findall(text):
        if fact_tier(key) in paused:
            findings.append(f"bound price or scope of a paused tier: {key}")
    plain = html.unescape(re.sub(r"<script\b.*?</script>|<!--.*?-->", "", text, flags=re.S))
    plain = re.sub(r"<[^>]+>", " ", plain)
    for line in plain.splitlines():
        if re.search(r"planned|proposed|/mo\b", line, re.I):
            continue
        for tier in paused:
            if re.search(rf"\brequest {tier}\b", line, re.I):
                findings.append(f"request call to action for a paused tier: {line.strip()}")
            if re.search(rf"\b{tier}\b[^.\n]{{0,40}}\$\d|\$[\d,]+[^.\n]{{0,20}}\b{tier}\b", line, re.I):
                findings.append(f"price of a paused tier: {line.strip()}")
        if len(paused) == len(PAID_TIERS):
            match = PAID_CTA.search(line)
            if match:
                findings.append(f"paid report call to action: {match[0]}")
    return findings


def served_files(root: Path) -> list[tuple[Path, str, Path]]:
    """(path, relative served name, stored-variant path) for every checked content file."""
    files = [
        (path, path.relative_to(root / SITE).as_posix(), root / OFFERS / "site" / path.relative_to(root / SITE))
        for path in sorted((root / SITE).rglob("*"))
        if path.suffix in {".html", ".txt", ".md"}
    ]
    return files + [(root / README, README.as_posix(), root / OFFERS / "repo" / README)]


def run(root: Path = ROOT, *, write: bool = False) -> int:
    facts = product_facts(root)
    validate_offer_terms(root, facts)
    offers = json.loads((root / OFFERS / "offers.json").read_text(encoding="utf-8"))
    pending = {root / SNAPSHOT: json.dumps(facts, ensure_ascii=False, indent=2) + "\n"}
    scanned = 0
    stores = set()
    for path, relative, store in served_files(root):
        scanned += relative != README.as_posix()
        text = path.read_text(encoding="utf-8")
        variants = None
        if store.is_file():
            stores.add(store)
            variants = parse_variants(store.read_text(encoding="utf-8"), store.relative_to(root).as_posix())
            unused = set(variants) - {name for _, name, _ in OFFER.findall(text)}
            if unused:
                raise ValueError(f"{relative}: stored offer variants with no served marker: {sorted(unused)}")
        markdown = relative.endswith(".md")
        rendered = render(text, facts, variants, offers, markdown=markdown)
        if markdown and markdown_marker_findings(rendered):
            raise ValueError(f"{relative}: {markdown_marker_findings(rendered)[0]}")
        keys = {match[0] for match in FACT.findall(rendered)}
        required = {
            key for key in REQUIRED.get(relative, set()) if fact_tier(key) is None or tier_open(facts, fact_tier(key))
        }
        if required - keys:
            raise ValueError(f"{relative}: missing required fact bindings")
        if relative == "llms.txt" and "<!-- product-block:snapshot -->" not in text:
            raise ValueError("llms.txt: missing generated snapshot")
        if relative == "audit.html" and text.count("data-product-offers>") != 1:
            raise ValueError("audit.html: missing or ambiguous service offer binding")
        if relative in REQUIRED:
            # Catch newly copied obvious offer claims as well as broken markers.
            # History and unrelated proposed subscription prices are not rewritten.
            unbound = FACT.sub("", rendered)
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
        findings = paused_offer_findings(relative, rendered, facts)
        if findings:
            raise ValueError(f"{relative}: presents a paused PR Replay tier: {findings[0]}")
        pending[path] = rendered
    if not scanned or not all((root / SITE / name).is_file() for name in REQUIRED):
        raise ValueError("Refusing missing or empty required site scope")
    orphans = {path for path in (root / OFFERS).rglob("*") if path.is_file() and path.name != "offers.json"} - stores
    if orphans:
        raise ValueError(f"Stored offer variants for no served file: {sorted(p.name for p in orphans)}")
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
