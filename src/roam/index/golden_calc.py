"""Golden-master calculation corpus: extract, audit, and check (value oracle).

The top rung of the calc-faithfulness ladder. Static analysis (calc-inventory /
calc_divergence) and empirical rounding probes (calc-probe) bound *how* code
rounds; this module grounds *what the numbers must be*: real historical records
that co-store a calculation's raw inputs AND its produced outputs are extracted
into a deterministic corpus of ``(inputs -> expected outputs)`` cases, which then
serves as an exogenous oracle — replay the cases through a candidate
implementation (a rounding rule, or any external runner) and assert cent-exact
agreement. Characterization testing (Feathers) applied to money math: the corpus
asserts *sameness with the historical system*, not correctness against a spec.

Exactness contract: every numeric travels as ``decimal.Decimal`` parsed from the
source's own decimal *string* (DBF ``N``/``F`` fields are ASCII decimal text; CSV
cells likewise). No value ever passes through a binary float — the oracle must
not inherit IEEE-754 representation artifacts (``1.005`` is not ``1.005`` as a
float; it IS as a string).

Sources: legacy Visual FoxPro / dBase ``.dbf`` tables (the legacy-world
workhorse; minimal reader below, no third-party dependency), plus CSV and JSONL
for everything else. All reading is fail-open ``[]``-on-miss and read-only.
"""

from __future__ import annotations

import csv
import json
import re
import struct
from dataclasses import dataclass, field
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
)
from pathlib import Path
from typing import Callable, Iterator

# ---------------------------------------------------------------------------
# Minimal DBF reader (dBase III / Visual FoxPro), read-only, stdlib-only
# ---------------------------------------------------------------------------

_DELETED_FLAG = 0x2A  # '*' — record marked deleted; 0x20 ' ' = active


@dataclass(frozen=True)
class DbfField:
    name: str
    type: str  # C/N/F/D/L/I/Y/T/B/M
    size: int
    dec: int
    offset: int  # byte offset within the record (after the deletion flag)


def read_dbf_header(fh) -> tuple[int, int, int, list[DbfField]]:
    """(record_count, header_size, record_size, fields) from an open binary file."""
    header = fh.read(32)
    if len(header) < 32:
        raise ValueError("not a DBF file (short header)")
    record_count = struct.unpack("<I", header[4:8])[0]
    header_size = struct.unpack("<H", header[8:10])[0]
    record_size = struct.unpack("<H", header[10:12])[0]
    fields: list[DbfField] = []
    offset = 1  # skip the deletion flag byte
    while True:
        fd = fh.read(32)
        if len(fd) < 32 or fd[0] == 0x0D:
            break
        name = fd[0:11].split(b"\x00")[0].decode("ascii", "replace").strip()
        fields.append(DbfField(name=name, type=chr(fd[11]), size=fd[16], dec=fd[17], offset=offset))
        offset += fd[16]
    if not fields:
        raise ValueError("not a DBF file (no field descriptors)")
    return record_count, header_size, record_size, fields


def _decode_dbf_value(raw: bytes, fld: DbfField, encoding: str) -> str | None:
    """Decode one field to a STRING (numerics keep their exact decimal text).

    Returning text (not float) is the exactness contract: the caller parses
    numerics with ``Decimal`` so no value passes through IEEE-754.
    """
    if fld.type == "C":
        return raw.decode(encoding, "replace").strip()
    if fld.type in ("N", "F"):
        s = raw.decode("ascii", "replace").strip()
        # '*' anywhere marks dBase/VFP numeric overflow — the stored digits are
        # unusable, not merely padded; partial forms like '**12.34' must be None
        # (a junk string here would silently vanish downstream at Decimal-parse).
        if not s or s in (".",) or "*" in s:
            return None
        return s
    if fld.type == "D":
        s = raw.decode("ascii", "replace").strip()
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None
    if fld.type == "L":
        c = raw[:1].decode("ascii", "replace")
        return "true" if c in "YyTt" else ("false" if c in "NnFf" else None)
    if fld.type == "I" and len(raw) == 4:
        return str(struct.unpack("<i", raw)[0])
    if fld.type == "Y" and len(raw) == 8:  # VFP currency: int64 scaled 1e4
        return str(Decimal(struct.unpack("<q", raw)[0]) / Decimal(10000))
    # T (datetime), B (double), M (memo pointer) — not needed for golden cases
    return None


def iter_dbf_records(
    path: str | Path,
    columns: list[str] | None = None,
    encoding: str = "cp1253",
) -> Iterator[dict[str, str | None]]:
    """Yield active (non-deleted) records as {column: string-value} dicts.

    ``columns`` narrows decoding to the named fields (case-insensitive) — on a
    2 GB table decoding 6 of 89 fields is the difference between seconds and
    minutes. Unknown requested columns raise ``KeyError`` up front (a mapping
    typo must fail loudly, not yield empty cases).
    """
    p = Path(path)
    with p.open("rb") as fh:
        record_count, header_size, record_size, fields = read_dbf_header(fh)
        by_name = {f.name.upper(): f for f in fields}
        if columns is not None:
            missing = [c for c in columns if c.upper() not in by_name]
            if missing:
                raise KeyError(f"DBF {p.name} has no column(s) {missing}; available: {sorted(by_name)}")
            # key the yielded dicts by the REQUESTED spelling: validation is
            # case-insensitive, so lookups downstream (mappings, --where) must
            # see the caller's names — otherwise `net=netvalue` against a table
            # storing NETVALUE silently extracts zero cases.
            wanted = [(c, by_name[c.upper()]) for c in columns]
        else:
            wanted = [(f.name, f) for f in fields]
        fh.seek(header_size)
        emitted = 0
        while emitted < record_count:
            rec = fh.read(record_size)
            if len(rec) < record_size or rec[:1] == b"\x1a":
                break
            emitted += 1  # header record count bounds the read — trailing bytes
            # after the declared records (appender debris) must not become ghosts
            if rec[0] == _DELETED_FLAG:
                continue
            yield {name: _decode_dbf_value(rec[f.offset : f.offset + f.size], f, encoding) for name, f in wanted}


# ---------------------------------------------------------------------------
# Generic tabular sources (CSV / JSONL) — same string-exactness contract
# ---------------------------------------------------------------------------


def iter_csv_records(path: str | Path, encoding: str = "utf-8") -> Iterator[dict[str, str | None]]:
    with Path(path).open("r", encoding=encoding, newline="") as fh:
        for row in csv.DictReader(fh):
            yield {k: (v.strip() if isinstance(v, str) and v.strip() != "" else None) for k, v in row.items()}


def iter_jsonl_records(path: str | Path, encoding: str = "utf-8") -> Iterator[dict[str, str | None]]:
    with Path(path).open("r", encoding=encoding) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            # normalize everything to strings — numbers via str() keeps the
            # JSON text's decimal form (json parses 1.005 to float; callers who
            # need exactness should store strings, which pass through as-is)
            yield {k: (str(v) if v is not None else None) for k, v in row.items()}


def iter_records(path: str | Path, encoding: str | None = None) -> Iterator[dict[str, str | None]]:
    """Dispatch by suffix: .dbf / .csv / .jsonl (default DBF encoding cp1253)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".dbf":
        return iter_dbf_records(path, encoding=encoding or "cp1253")
    if suffix == ".csv":
        return iter_csv_records(path, encoding=encoding or "utf-8")
    if suffix in (".jsonl", ".ndjson"):
        return iter_jsonl_records(path, encoding=encoding or "utf-8")
    raise ValueError(f"unsupported golden source type: {suffix} (want .dbf/.csv/.jsonl)")


# ---------------------------------------------------------------------------
# Corpus model: extract (inputs -> expect) cases
# ---------------------------------------------------------------------------


@dataclass
class GoldenCase:
    id: int
    bucket: str
    inputs: dict[str, str]
    expect: dict[str, str]
    # Input keys that were COMPUTED at extract via --derive (e.g. gross=net+vat).
    # A derived-from-expect input embeds the oracle's own answer, so it must be
    # stripped from any external `--runner` payload — else the implementation
    # under test is handed the result it is supposed to produce.
    derived: tuple[str, ...] = ()


@dataclass
class ExtractStats:
    read: int = 0
    kept: int = 0
    skipped_missing: int = 0  # a mapped column was empty
    buckets: dict[str, int] = field(default_factory=dict)


def parse_mapping(spec: str) -> dict[str, str]:
    """``"net=NETVALUE,rate=VATCATEGOR"`` -> {case_key: SOURCE_COLUMN}."""
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"mapping entry {part!r} must be case_key=SOURCE_COLUMN")
        key, col = part.split("=", 1)
        out[key.strip()] = col.strip()
    if not out:
        raise ValueError("empty mapping")
    return out


# A --derive expression: NEW_KEY = OPERAND (+|-) OPERAND, operands drawn from the
# case's inputs OR expect. Kept deliberately tiny (two operands, +/- only) so it
# stays Decimal-exact and cannot express anything that would hide a bug.
_DERIVE_RE = re.compile(r"^\s*(\w+)\s*=\s*(\w+)\s*([+-])\s*(\w+)\s*$")


def parse_derivations(spec: str) -> list[tuple[str, str, str, str]]:
    """``"gross=net+vat"`` -> [(new_key, lhs, op, rhs)]. Semicolon-separated."""
    out: list[tuple[str, str, str, str]] = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        m = _DERIVE_RE.match(part)
        if not m:
            raise ValueError(f"--derive entry {part!r} must be NEW=A+B or NEW=A-B (two operands, +/- only)")
        out.append((m.group(1), m.group(2), m.group(3), m.group(4)))
    return out


def apply_derivations(
    inputs: dict[str, str], expect: dict[str, str], derivations: list[tuple[str, str, str, str]]
) -> tuple[dict[str, str], tuple[str, ...]] | None:
    """Compute derived inputs Decimal-exactly. Operands resolve from inputs first,
    then expect. Returns (augmented_inputs, derived_key_names), or None if any
    operand is missing/non-numeric (the case cannot carry the derived value)."""
    inputs = dict(inputs)
    derived: list[str] = []
    for new_key, lhs, op, rhs in derivations:

        def val(name):
            return to_decimal(inputs.get(name, expect.get(name, "")))

        a, b = val(lhs), val(rhs)
        if a is None or b is None:
            return None
        result = a + b if op == "+" else a - b
        inputs[new_key] = str(result)
        derived.append(new_key)
    return inputs, tuple(derived)


def extract_cases(
    records: Iterator[dict[str, str | None]],
    case_map: dict[str, str],
    expect_map: dict[str, str],
    bucket_by: list[str],
    where: dict[str, str] | None = None,
    limit: int = 0,
    derivations: list[tuple[str, str, str, str]] | None = None,
) -> tuple[list[GoldenCase], ExtractStats]:
    """Build golden cases from records. A case needs EVERY mapped column non-empty
    (a record missing an expected output cannot serve as an oracle row).

    ``bucket_by`` accepts an input case_key OR the pseudo-key ``@index:N`` which
    buckets by kept-record ordinal // N (an ERA proxy: it reveals regime changes
    over the table's append order without a date column). Approximation caveat:
    the ordinal is over ACTIVE records in file order — a VFP PACK or re-sort
    shifts it, so it is a proxy for chronology, not chronology itself.

    ``derivations`` (from ``--derive``) computes extra inputs Decimal-exactly
    (e.g. gross=net+vat); the derived keys are recorded on each case so a
    ``--runner`` check can strip them (they may embed the expected answer)."""
    stats = ExtractStats()
    cases: list[GoldenCase] = []
    era_size = 0
    era_keys: list[str] = []
    plain_bucket_keys: list[str] = []
    for b in bucket_by:
        if b.startswith("@index:"):
            try:
                era_size = max(1, int(b.split(":", 1)[1]))
            except ValueError as exc:
                raise ValueError(f"bad era pseudo-key {b!r}; want @index:N (N a positive int)") from exc
            era_keys.append(b)
        else:
            plain_bucket_keys.append(b)
    for rec in records:
        stats.read += 1
        if where and any((rec.get(col) or "") != val for col, val in where.items()):
            continue
        inputs: dict[str, str | None] = {k: rec.get(col) for k, col in case_map.items()}
        expect = {k: rec.get(col) for k, col in expect_map.items()}
        if any(v is None for v in inputs.values()) or any(v is None for v in expect.values()):
            stats.skipped_missing += 1
            continue
        derived: tuple[str, ...] = ()
        if derivations:
            applied = apply_derivations(inputs, expect, derivations)  # type: ignore[arg-type]
            if applied is None:
                stats.skipped_missing += 1
                continue
            inputs, derived = applied
        parts = [str(inputs.get(b, "?")) for b in plain_bucket_keys]
        if era_size:
            parts.append(f"era{stats.kept // era_size}")
        bucket = "|".join(parts) if parts else "all"
        cases.append(GoldenCase(id=stats.kept, bucket=bucket, inputs=inputs, expect=expect, derived=derived))  # type: ignore[arg-type]
        stats.kept += 1
        stats.buckets[bucket] = stats.buckets.get(bucket, 0) + 1
        if limit and stats.kept >= limit:
            break
    return cases, stats


def write_corpus(cases: list[GoldenCase], out_path: str | Path) -> None:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for c in cases:
            row = {"id": c.id, "bucket": c.bucket, "inputs": c.inputs, "expect": c.expect}
            if c.derived:
                row["derived"] = list(c.derived)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_corpus(path: str | Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            cases.append(
                GoldenCase(
                    id=d["id"],
                    bucket=d.get("bucket", "all"),
                    inputs=d["inputs"],
                    expect=d["expect"],
                    derived=tuple(d.get("derived") or ()),
                )
            )
    return cases


# ---------------------------------------------------------------------------
# Rounding-rule registry (Decimal-exact) + audit + check
# ---------------------------------------------------------------------------

_CENT = Decimal("0.01")

# rule name -> Decimal rounding constant for quantize()
RULES: dict[str, str] = {
    "half_up": ROUND_HALF_UP,  # half away from zero (VFP/PHP default)
    "half_even": ROUND_HALF_EVEN,  # banker's
    "half_down": ROUND_HALF_DOWN,  # half toward zero
    "truncate": ROUND_DOWN,
    "ceil": ROUND_CEILING,
    "floor": ROUND_FLOOR,
}

# Naming bridge to calc-inventory's semantics labels (rounding_semantic()):
# golden's "half_up" is decimal ROUND_HALF_UP = half-AWAY-FROM-ZERO — it is NOT
# JS Math.round's half-toward-+infinity, which calc-inventory labels
# "half_up_toward_positive". Emitted in audit/check summaries so the two
# vocabularies can be machine-joined; note no RULES entry expresses JS's
# half-toward-+inf (a JS-produced corpus surfaces its negative ties as
# residuals — a documented audit gap, not a bug).
RULE_SEMANTICS: dict[str, str] = {
    "half_up": "half_away_from_zero",
    "half_even": "half_to_even",
    "half_down": "half_toward_zero",
    "truncate": "truncate",
    "ceil": "toward_positive",
    "floor": "toward_negative",
}


def to_decimal(s: str) -> Decimal | None:
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError):
        return None


def predict(rule: str, base: Decimal, rate_pct: Decimal) -> Decimal:
    """rule(base * rate% ) quantized to cents — the net-family oracle formula.

    Kept as the ``net`` family shorthand for ``check --rule`` and back-compat.
    The full family set (below) covers the gross-inclusive second paths a naive
    net×rate reimplementation misses."""
    raw = base * rate_pct / Decimal(100)
    return raw.quantize(_CENT, rounding=RULES[rule])


@dataclass(frozen=True)
class RuleFamily:
    """A candidate calculation SHAPE (which base, which formula), independent of
    the rounding mode. Reverse-engineering the legacy calc means fitting both the
    shape AND the mode — the same tie mode over the WRONG base still misses."""

    name: str
    base_role: str  # input case_key holding this family's base (e.g. 'net' or 'gross')
    fn: Callable[[Decimal, Decimal, str], Decimal]  # (base, rate_pct, rounding_const) -> cents


def _f_net(net: Decimal, rate: Decimal, mode: str) -> Decimal:
    # vat = round(net * rate%)
    return (net * rate / Decimal(100)).quantize(_CENT, rounding=mode)


def _f_vat_from_gross(gross: Decimal, rate: Decimal, mode: str) -> Decimal:
    # vat = round(gross * rate / (100+rate)) — VAT extracted directly from a
    # tax-inclusive gross (the entry mode where the operator typed the gross).
    return (gross * rate / (Decimal(100) + rate)).quantize(_CENT, rounding=mode)


def _f_net_from_gross(gross: Decimal, rate: Decimal, mode: str) -> Decimal:
    # net = round(gross * 100 / (100+rate)); vat = gross - net — the two-step
    # VFP path (net by division+round, vat by subtraction).
    net = (gross * Decimal(100) / (Decimal(100) + rate)).quantize(_CENT, rounding=mode)
    return gross - net


# Registry of calculation shapes. 'net' is the naive path; the two gross families
# are the tax-inclusive second paths that concentrate in later eras of real data
# (measured: they explain ~62% of the late-era residuals a net-only audit leaves).
FAMILIES: dict[str, RuleFamily] = {
    "net": RuleFamily("net", "net", _f_net),
    "vat_from_gross": RuleFamily("vat_from_gross", "gross", _f_vat_from_gross),
    "net_from_gross": RuleFamily("net_from_gross", "gross", _f_net_from_gross),
}


def predict_family(family: str, mode: str, base: Decimal, rate_pct: Decimal) -> Decimal:
    """Cents predicted by a (family, mode) rule."""
    return FAMILIES[family].fn(base, rate_pct, RULES[mode])


def resolve_rate(case: GoldenCase, rate_key: str, rate_map: dict[str, str] | None) -> Decimal | None:
    """Rate percent for a case: the raw field value, optionally via a code map
    (e.g. VAT category code 1 -> 24)."""
    raw = case.inputs.get(rate_key)
    if raw is None:
        return None
    if rate_map is not None:
        raw = rate_map.get(str(raw).strip())
        if raw is None:
            return None
    return to_decimal(raw)


@dataclass
class RuleFit:
    rule: str
    matched: int
    total: int

    @property
    def pct(self) -> float:
        return (100.0 * self.matched / self.total) if self.total else 0.0


def audit_corpus(
    cases: list[GoldenCase],
    base_key: str,
    rate_key: str,
    target_key: str,
    rate_map: dict[str, str] | None = None,
    residual_examples: int = 5,
    role_keys: dict[str, str] | None = None,
) -> dict:
    """Which calculation SHAPE + rounding rule best explains each bucket's outputs?

    Reverse-engineers the legacy calc from its own data: per bucket, fit every
    ``(family, mode)`` whose family base is available, and report match rates
    plus the residual cases NO rule explains — the multi-path/gross-inclusive
    tell a naive net×rate reimplementation silently diverges on.

    ``role_keys`` maps a family base_role -> the input case_key holding it
    (default ``{"net": base_key}``). Provide ``{"gross": "gross"}`` (via
    ``--derive gross=net+vat``) to also fit the tax-inclusive second paths.

    Back-compat: ``rule_match_pct`` / ``best_rule`` / ``unexplained_by_best_rule``
    stay net-family-only (plain mode names). ``family_match_pct`` /
    ``best_family`` / ``unexplained_by_families`` add the full family view.
    """
    roles = role_keys or {"net": base_key}
    # (family, mode) rules whose base role is resolvable from this corpus
    active_families = [f for f in FAMILIES.values() if f.base_role in roles]

    by_bucket: dict[str, list[GoldenCase]] = {}
    for c in cases:
        by_bucket.setdefault(c.bucket, []).append(c)
    buckets_out: list[dict] = []
    total_evaluated = 0
    total_unexplained = 0  # by net-family best rule (back-compat)
    total_unexplained_families = 0  # by best across ALL families
    for bucket, group in sorted(by_bucket.items()):
        net_fits = {rule: 0 for rule in RULES}
        fam_fits = {f"{f.name}:{mode}": 0 for f in active_families for mode in RULES}
        evaluated = 0
        residuals: list[dict] = []
        for c in group:
            rate = resolve_rate(c, rate_key, rate_map)
            expect = to_decimal(c.expect.get(target_key, ""))
            net_base = to_decimal(c.inputs.get(base_key, ""))
            if rate is None or expect is None or net_base is None:
                continue
            evaluated += 1
            for rule in RULES:
                if predict(rule, net_base, rate) == expect:
                    net_fits[rule] += 1
            fam_explained = False
            for fam in active_families:
                fbase = to_decimal(c.inputs.get(roles[fam.base_role], ""))
                if fbase is None:
                    continue
                for mode in RULES:
                    if fam.fn(fbase, rate, RULES[mode]) == expect:
                        fam_fits[f"{fam.name}:{mode}"] += 1
                        fam_explained = True
            if not fam_explained and len(residuals) < residual_examples:
                residuals.append(
                    {
                        "id": c.id,
                        base_key: str(net_base),
                        "rate_pct": str(rate),
                        "expected": str(expect),
                        "half_up_prediction": str(predict("half_up", net_base, rate)),
                    }
                )
        best_net = max(net_fits, key=lambda r: net_fits[r]) if evaluated else None
        best_fam = max(fam_fits, key=lambda r: fam_fits[r]) if (evaluated and fam_fits) else None
        buckets_out.append(
            {
                "bucket": bucket,
                "cases": evaluated,
                "best_rule": best_net,
                "best_match_pct": round(100.0 * net_fits[best_net] / evaluated, 2) if evaluated else 0.0,
                "rule_match_pct": {r: round(100.0 * n / evaluated, 2) for r, n in net_fits.items()}
                if evaluated
                else {},
                "best_family": best_fam,
                "best_family_match_pct": round(100.0 * fam_fits[best_fam] / evaluated, 2)
                if (evaluated and best_fam)
                else 0.0,
                "family_match_pct": {r: round(100.0 * n / evaluated, 2) for r, n in fam_fits.items()}
                if evaluated
                else {},
                "residual_examples": residuals,
            }
        )
        total_evaluated += evaluated
        total_unexplained += (evaluated - max(net_fits.values())) if evaluated else 0
        total_unexplained_families += (evaluated - (max(fam_fits.values()) if fam_fits else 0)) if evaluated else 0
    return {
        "buckets": buckets_out,
        "cases_evaluated": total_evaluated,
        "unexplained_by_best_rule": total_unexplained,
        "unexplained_by_families": total_unexplained_families,
        "families_fitted": [f.name for f in active_families],
    }


@dataclass
class CheckResult:
    total: int
    replayed: int
    passed: int
    failures: list[dict]
    buckets: dict[str, dict]  # bucket -> {replayed, passed}
    missing: int = 0  # runner mode: cases sent but never answered — a runner defect

    @property
    def failed(self) -> int:
        return self.replayed - self.passed


def _compare_case(expect: dict[str, str], got: dict, tolerance: Decimal) -> tuple[bool, dict]:
    """Cent-exact (|Δ| <= tolerance) comparison of every expected output field."""
    deltas: dict[str, str] = {}
    ok = True
    for key, want_s in expect.items():
        want = to_decimal(want_s)
        got_v = to_decimal(str(got.get(key))) if got.get(key) is not None else None
        if want is None:
            continue
        if got_v is None or abs(got_v - want) > tolerance:
            ok = False
            deltas[key] = f"want {want} got {got.get(key)!r}"
    return ok, deltas


def check_with_rule(
    cases: list[GoldenCase],
    rule: str,
    base_key: str,
    rate_key: str,
    target_key: str,
    rate_map: dict[str, str] | None = None,
    tolerance: Decimal = Decimal("0.001"),
    max_failures: int = 20,
) -> CheckResult:
    """Oracle via a named rounding rule — corpus vs ``rule(base × rate%)``."""
    result = CheckResult(total=len(cases), replayed=0, passed=0, failures=[], buckets={})
    for c in cases:
        base = to_decimal(c.inputs.get(base_key, ""))
        rate = resolve_rate(c, rate_key, rate_map)
        if base is None or rate is None:
            continue
        got = {target_key: str(predict(rule, base, rate))}
        result.replayed += 1
        b = result.buckets.setdefault(c.bucket, {"replayed": 0, "passed": 0})
        b["replayed"] += 1
        ok, deltas = _compare_case({target_key: c.expect.get(target_key, "")}, got, tolerance)
        if ok:
            result.passed += 1
            b["passed"] += 1
        elif len(result.failures) < max_failures:
            result.failures.append({"id": c.id, "bucket": c.bucket, "inputs": c.inputs, "deltas": deltas})
    return result


def check_with_runner(
    cases: list[GoldenCase],
    runner_argv: list[str],
    tolerance: Decimal = Decimal("0.001"),
    max_failures: int = 20,
    timeout: int = 600,
) -> CheckResult:
    """Oracle via an external runner — the general seam for any implementation.

    Contract: the runner is spawned ONCE; it receives one JSON object per line
    on stdin ``{"id": N, "inputs": {...}}`` and must emit one JSON object per
    line on stdout ``{"id": N, "<expect-field>": value, ...}`` (any order).
    One process for the whole corpus — 100k cases must not mean 100k spawns.

    Every case is sent, so a case the runner never answers is a RUNNER DEFECT,
    counted in ``missing`` (the caller gates on it — a runner that silently
    drops the hard buckets must never read green). Duplicate ids: last wins.
    """
    import subprocess

    result = CheckResult(total=len(cases), replayed=0, passed=0, failures=[], buckets={})
    if not cases:
        return result

    def _payload_inputs(c: GoldenCase) -> dict[str, str]:
        # strip --derive'd inputs: a derived-from-expect value (gross=net+vat)
        # would hand the oracle's answer to the implementation under test.
        if not c.derived:
            return c.inputs
        return {k: v for k, v in c.inputs.items() if k not in c.derived}

    payload = (
        "\n".join(json.dumps({"id": c.id, "inputs": _payload_inputs(c)}, ensure_ascii=False) for c in cases) + "\n"
    )
    try:
        proc = subprocess.run(runner_argv, input=payload, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.missing = len(cases)
        result.failures.append({"id": -1, "bucket": "-", "inputs": {}, "deltas": {"runner": f"failed to run: {exc}"}})
        return result
    got_by_id: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            got_by_id[int(d["id"])] = d
        except (ValueError, KeyError, TypeError):
            continue
    missing_ids: list[int] = []
    for c in cases:
        got = got_by_id.get(c.id)
        if got is None:
            missing_ids.append(c.id)
            continue
        result.replayed += 1
        b = result.buckets.setdefault(c.bucket, {"replayed": 0, "passed": 0})
        b["replayed"] += 1
        ok, deltas = _compare_case(c.expect, got, tolerance)
        if ok:
            result.passed += 1
            b["passed"] += 1
        elif len(result.failures) < max_failures:
            result.failures.append({"id": c.id, "bucket": c.bucket, "inputs": c.inputs, "deltas": deltas})
    result.missing = len(missing_ids)
    if missing_ids and len(result.failures) < max_failures:
        result.failures.append(
            {
                "id": missing_ids[0],
                "bucket": "-",
                "inputs": {},
                "deltas": {"runner": f"{len(missing_ids)} case(s) never answered; first ids: {missing_ids[:10]}"},
            }
        )
    if (result.replayed == 0 or proc.returncode != 0) and proc.stderr:
        # a crashing/failing runner must leave a diagnostic, not just a count
        result.failures.append(
            {
                "id": -1,
                "bucket": "-",
                "inputs": {},
                "deltas": {"runner": f"exit {proc.returncode}; stderr tail: {proc.stderr[-500:]}"},
            }
        )
    return result
