#!/usr/bin/env python3
"""Fail the build when published retrieval numbers and measured ones disagree.

`bench/retrieve/SUBMISSION.md` is a public file whose whole purpose is a
credibility claim, and it opens by telling a reader to run a command. For
three minor versions it published `recall@20 = 0.903` measured at v12.3
while `README.md` published `0.503` measured at a commit predating the
v12.1-v12.3 retrieval work. Two numbers, neither re-derived, one of them
wrong by 41 points -- and nothing in CI could tell.

This is the guard. It re-reads the numbers the docs publish and compares
them against a measurement, and it fails **in both directions**:

* measured **below** published -> a retrieval regression, or a stale index
  (see the incremental-FTS defect documented in SUBMISSION.md);
* measured **above** published -> the docs went stale while the retriever
  improved. That is the direction this repo actually drifted, and a
  one-sided floor gate is blind to it.

Usage
-----

    roam --json eval-retrieve --tasks bench/retrieve/roam_self.jsonl > eval.json
    python bench/retrieve/check_published_recall.py --eval-json eval.json

Exit codes: 0 pass, 1 drift detected, 2 the check itself could not run
(missing file, unparseable envelope, no canonical block). An absent
measurement is never a pass.
"""

# ruff: noqa: T201, T203
# This is a CI gate: its entire output contract is stdout text and
# ``::error::`` annotations that a human reads in a workflow log. Same
# rationale as the ``scripts/**/*.py`` exemption in pyproject.toml, scoped to
# this file so no shared config has to change for it.

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: Absolute drift permitted per K, in either direction.
#:
#: Re-derived 2026-08-12. The previous rationale here read:
#:
#:     "CI clones at ``fetch-depth: 50`` while the reference measurement used
#:      full history; emptying the ``git_cochange`` table outright -- a
#:      strictly larger perturbation than a shallow clone -- moved recall@20
#:      by 0.011 on this bench."
#:
#: Both halves of that were measured again at 74520ca5, same binary, same
#: corpus, index byte-identical across arms, and both were wrong:
#:
#:   full history (2575 commits)  recall@20 = 0.9139
#:   ``git_cochange`` emptied     recall@20 = 0.8778   (-0.0361, not -0.011)
#:   ``fetch-depth: 50``          recall@20 = 0.8500   (-0.0639)
#:
#: A shallow clone is NOT the milder perturbation -- it scores below an empty
#: table, because 50 commits give a partial, recency-biased co-change signal
#: that mis-ranks, where an absent one degrades to a clean fallback.
#:
#: The consequence was not academic. This tolerance was sized to absorb an
#: environmental offset of ~0.011 and was in fact absorbing ~0.064, so the
#: gate could not have detected any real regression at all: its whole budget
#: was pre-spent. ``.github/workflows/dogfood.yml`` now checks out at
#: ``fetch-depth: 0`` so the measurement matches the environment the docs
#: publish, which returns the full budget to its actual purpose.
#:
#: The number is unchanged at 0.06 -- headroom for platform and tokenizer
#: differences. For scale, the drift that motivated this gate was 0.133.
DEFAULT_TOLERANCE = 0.06

#: Fraction of the tolerance at which a still-passing drift is called out.
#:
#: A pure pass/fail gate reports nothing until the moment it fails, so a
#: systematic offset can sit at 80% of budget for days and read as healthy.
#: That is exactly what happened: recall@20 drift held at -0.047 (78% of
#: tolerance) from 2026-08-06 to 2026-08-11 across at least five main
#: commits, every one of them green, and the first signal anyone got was a
#: red build. This threshold turns that silent band into a warning.
#:
#: Advisory only -- it never changes the exit code, so it cannot fail a build
#: on its own.
NEAR_MISS_FRACTION = 0.7

#: Docs whose published numbers must agree with the measurement. Every file
#: here needs a ``canonical-recall`` block; a file that lost its block is an
#: error, not a skip.
PUBLISHING_DOCS = ("SUBMISSION.md", "README.md")

_BLOCK_RE = re.compile(
    r"<!--\s*canonical-recall:begin.*?-->(?P<body>.*?)<!--\s*canonical-recall:end\s*-->",
    re.DOTALL,
)
_RECALL_RE = re.compile(r"recall@(?P<k>\d+)\s*=\s*(?P<value>[0-9]*\.?[0-9]+)")


class CheckError(RuntimeError):
    """The check could not be performed. Never silently downgraded to a pass."""


def published_recalls(path: Path) -> dict[int, float]:
    """Parse the canonical-recall block out of a published Markdown doc."""
    if not path.is_file():
        raise CheckError(f"{path} does not exist - cannot verify what it publishes.")
    match = _BLOCK_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise CheckError(
            f"{path} has no <!-- canonical-recall:begin --> / :end block. "
            "Either the block was removed or the file stopped publishing numbers; "
            "both need a human, so this is a failure rather than a skip."
        )
    found = {int(m.group("k")): float(m.group("value")) for m in _RECALL_RE.finditer(match.group("body"))}
    if not found:
        raise CheckError(f"{path} canonical-recall block contains no `recall@K = V` lines.")
    return found


def measured_recalls(eval_json: Path) -> tuple[dict[int, float], int]:
    """Read the aggregate out of a ``roam --json eval-retrieve`` envelope.

    Tolerates the non-JSON preamble that ``--emit-format coderag|beir``
    prints ahead of the envelope even under ``--json`` (a known defect,
    recorded in SUBMISSION.md) by skipping to the first ``{``.
    """
    if not eval_json.is_file():
        raise CheckError(f"eval JSON not found: {eval_json}")
    raw = eval_json.read_text(encoding="utf-8")
    brace = raw.find("{")
    if brace == -1:
        raise CheckError(f"{eval_json} contains no JSON object.")
    try:
        envelope = json.loads(raw[brace:])
    except json.JSONDecodeError as exc:
        raise CheckError(f"{eval_json} is not a parseable JSON envelope: {exc}") from exc

    if envelope.get("command") != "eval-retrieve":
        raise CheckError(f"{eval_json} is not an eval-retrieve envelope (command={envelope.get('command')!r}).")
    summary = envelope.get("summary")
    if not isinstance(summary, dict):
        raise CheckError(f"{eval_json} envelope has no summary object.")
    if summary.get("partial_success"):
        raise CheckError(
            "eval-retrieve reported partial_success - the run was degraded, "
            "so its numbers cannot confirm or refute the published ones."
        )

    recalls: dict[int, float] = {}
    for key, value in summary.items():
        m = re.fullmatch(r"recall_at_(\d+)", key)
        if m and isinstance(value, (int, float)):
            recalls[int(m.group(1))] = float(value)
    if not recalls:
        raise CheckError(f"{eval_json} summary carries no recall_at_K keys: {sorted(summary)}")

    task_count = summary.get("task_count")
    if not isinstance(task_count, int) or task_count <= 0:
        raise CheckError(f"eval-retrieve reported task_count={task_count!r}; refusing to compare against an empty run.")
    return recalls, task_count


def near_misses(
    measured: dict[int, float],
    published: dict[int, float],
    source: str,
    tolerance: float,
    fraction: float = NEAR_MISS_FRACTION,
) -> list[str]:
    """Return one line per K that passes but has eaten most of the budget.

    Strictly between ``fraction * tolerance`` and ``tolerance``: anything at
    or past ``tolerance`` is already a failure and is reported as one by
    :func:`compare`, so a K never appears in both lists.

    This exists because a binary gate is silent right up to the moment it
    breaks. A drift parked at 78% of tolerance for five days looks exactly
    like a drift of zero in the log, and the difference between them is the
    difference between "healthy" and "one commit of noise from red".
    """
    warnings: list[str] = []
    if tolerance <= 0:
        return warnings
    floor = fraction * tolerance
    for k in sorted(published):
        if k not in measured:
            continue
        drift = measured[k] - published[k]
        if floor <= abs(drift) <= tolerance:
            pct = abs(drift) / tolerance * 100
            warnings.append(
                f"{source}: recall@{k} is within tolerance but has consumed "
                f"{pct:.0f}% of it - published {published[k]:.4f}, "
                f"measured {measured[k]:.4f}, drift {drift:+.4f} "
                f"(tolerance +/-{tolerance:g})"
            )
    return warnings


def compare(
    measured: dict[int, float],
    published: dict[int, float],
    source: str,
    tolerance: float,
) -> list[str]:
    """Return one failure line per K that drifted past *tolerance*."""
    failures: list[str] = []
    for k in sorted(published):
        if k not in measured:
            failures.append(f"{source}: publishes recall@{k} but the harness did not measure it.")
            continue
        drift = measured[k] - published[k]
        if abs(drift) > tolerance:
            direction = "REGRESSED" if drift < 0 else "IMPROVED (docs are stale)"
            # 4 dp: the harness reports 4, the docs publish 3. Printing 3 here
            # can render a real failure as "published 0.642, measured 0.642",
            # which reads as a gate bug rather than the drift it is.
            failures.append(
                f"{source}: recall@{k} {direction} - "
                f"published {published[k]:.4f}, measured {measured[k]:.4f}, "
                f"drift {drift:+.4f} exceeds tolerance +/-{tolerance:g}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--eval-json",
        type=Path,
        required=True,
        help="Output of `roam --json eval-retrieve --tasks bench/retrieve/roam_self.jsonl`.",
    )
    parser.add_argument(
        "--bench-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory holding the published docs. Default: this script's directory.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"Absolute drift permitted per K, either direction. Default: {DEFAULT_TOLERANCE}.",
    )
    args = parser.parse_args(argv)

    if args.tolerance < 0:
        print(f"::error::negative tolerance {args.tolerance} is meaningless", file=sys.stderr)
        return 2

    try:
        measured, task_count = measured_recalls(args.eval_json)
        docs = {name: published_recalls(args.bench_dir / name) for name in PUBLISHING_DOCS}
    except CheckError as exc:
        print(f"::error::published-recall check could not run: {exc}", file=sys.stderr)
        return 2

    measured_str = "  ".join(f"recall@{k}={v:.3f}" for k, v in sorted(measured.items()))
    print(f"Measured ({task_count} tasks): {measured_str}")
    for name, values in docs.items():
        published_str = "  ".join(f"recall@{k}={v:.3f}" for k, v in sorted(values.items()))
        print(f"Published in {name}: {published_str}")
    print(f"Tolerance: +/-{args.tolerance:.3f} absolute, both directions")

    failures: list[str] = []
    for name, values in docs.items():
        failures.extend(compare(measured, values, name, args.tolerance))

    # The two docs must also agree with each other, or "which number is real"
    # is undecidable no matter what the harness says. This is the three-way
    # disagreement that shipped.
    ks = sorted({k for values in docs.values() for k in values})
    for k in ks:
        quoted = {name: values[k] for name, values in docs.items() if k in values}
        if len({round(v, 6) for v in quoted.values()}) > 1:
            detail = ", ".join(f"{name}={v:.3f}" for name, v in sorted(quoted.items()))
            failures.append(f"published docs disagree with each other on recall@{k}: {detail}")

    if failures:
        print()
        print(f"::error::published retrieval numbers do not match measurement ({len(failures)} problem(s))")
        for line in failures:
            print(f"  FAIL  {line}")
        print()
        print("Fix by re-measuring against a FRESH index and updating the")
        print("canonical-recall blocks in bench/retrieve/{README,SUBMISSION}.md,")
        print("including the commit SHA and date. A stale index alone costs ~13")
        print("points of recall@20 - see SUBMISSION.md 'Known defects'.")
        return 1

    # Passing, but say HOW passing. A gate that prints the same "OK" at 0.001
    # of drift and at 0.059 hides the approach to its own cliff -- which is
    # how a -0.047 offset stayed invisible for five days of green builds.
    warnings: list[str] = []
    for name, values in docs.items():
        warnings.extend(near_misses(measured, values, name, args.tolerance))
    if warnings:
        print()
        print(f"::warning::published retrieval numbers still pass, but {len(warnings)} are near the tolerance edge")
        for line in warnings:
            print(f"  NEAR  {line}")
        print()
        print("Not a failure, and not noise either: a drift parked near the")
        print("edge means the next ordinary corpus change fails this gate, and")
        print("that a real regression of the remaining size cannot be seen.")
        print("Re-measure against a fresh full-history index and republish.")

    print()
    print(f"OK - published numbers match measurement within +/-{args.tolerance:.3f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
