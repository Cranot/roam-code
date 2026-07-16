"""Tests for ``roam calc-golden`` — the golden-master value oracle (L4)."""

from __future__ import annotations

import json
import struct
import sys
from decimal import Decimal

from click.testing import CliRunner

from roam.commands.cmd_calc_golden import calc_golden
from roam.index.golden_calc import (
    GoldenCase,
    audit_corpus,
    check_with_rule,
    check_with_runner,
    extract_cases,
    iter_dbf_records,
    parse_mapping,
    predict,
    read_corpus,
    to_decimal,
    write_corpus,
)

# ---------------------------------------------------------------------------
# synthetic DBF builder (dBase III layout) — no fixture files needed
# ---------------------------------------------------------------------------


def _make_dbf(path, fields, rows, deleted_rows=()):
    """fields: [(name, type, size, dec)]; rows: list of dicts of STRING values."""
    record_size = 1 + sum(size for _, _, size, _ in fields)
    header_size = 32 + 32 * len(fields) + 1
    all_rows = list(rows) + list(deleted_rows)
    buf = bytearray()
    buf += bytes([0x03])  # dBase III, no memo
    buf += bytes([26, 7, 15])  # last-update stamp YY MM DD (any fixed value)
    buf += struct.pack("<I", len(all_rows))
    buf += struct.pack("<H", header_size)
    buf += struct.pack("<H", record_size)
    buf += b"\x00" * 20
    for name, ftype, size, dec in fields:
        fd = bytearray(32)
        fd[0:11] = name.encode("ascii").ljust(11, b"\x00")
        fd[11] = ord(ftype)
        fd[16] = size
        fd[17] = dec
        buf += fd
    buf += b"\x0d"
    for i, row in enumerate(all_rows):
        deleted = i >= len(rows)
        buf += b"*" if deleted else b" "
        for name, ftype, size, dec in fields:
            val = str(row.get(name, ""))
            raw = val.rjust(size) if ftype in ("N", "F") else val.ljust(size)
            buf += raw.encode("ascii", "replace")[:size]
    buf += b"\x1a"
    path.write_bytes(bytes(buf))
    return path


_FIELDS = [("NETVALUE", "N", 12, 2), ("VATCAT", "N", 2, 0), ("VATAMT", "N", 12, 2), ("DOCTYPE", "C", 5, 0)]


def _dbf_with_cases(tmp_path, rows, deleted=()):
    return _make_dbf(tmp_path / "t.dbf", _FIELDS, rows, deleted)


# ---------------------------------------------------------------------------
# DBF reader
# ---------------------------------------------------------------------------


def test_dbf_reader_exact_strings_and_deleted_skip(tmp_path):
    p = _dbf_with_cases(
        tmp_path,
        rows=[{"NETVALUE": "100.00", "VATCAT": "1", "VATAMT": "24.00", "DOCTYPE": "1.1"}],
        deleted=[{"NETVALUE": "999.00", "VATCAT": "1", "VATAMT": "1.00", "DOCTYPE": "x"}],
    )
    recs = list(iter_dbf_records(p))
    assert len(recs) == 1  # deleted row skipped
    assert recs[0]["NETVALUE"] == "100.00"  # exact decimal TEXT, not float
    assert recs[0]["DOCTYPE"] == "1.1"


def test_dbf_reader_column_narrowing_and_loud_typo(tmp_path):
    p = _dbf_with_cases(tmp_path, rows=[{"NETVALUE": "5.00", "VATCAT": "2", "VATAMT": "0.65", "DOCTYPE": "1.1"}])
    recs = list(iter_dbf_records(p, columns=["NETVALUE", "VATAMT"]))
    assert set(recs[0]) == {"NETVALUE", "VATAMT"}
    try:
        list(iter_dbf_records(p, columns=["NOSUCH"]))
        raise AssertionError("expected KeyError")
    except KeyError as exc:
        assert "NOSUCH" in str(exc)


# ---------------------------------------------------------------------------
# extraction + corpus round-trip
# ---------------------------------------------------------------------------


def _records(*rows):
    return iter(rows)


def test_extract_cases_mapping_bucket_where_and_missing():
    rows = [
        {"NET": "100.00", "CAT": "1", "VAT": "24.00", "TYPE": "1.1"},
        {"NET": "50.00", "CAT": "2", "VAT": None, "TYPE": "1.1"},  # missing expect -> skipped
        {"NET": "10.00", "CAT": "1", "VAT": "2.40", "TYPE": "5.1"},  # filtered by where
    ]
    cases, stats = extract_cases(
        _records(*rows),
        case_map=parse_mapping("net=NET,rate=CAT"),
        expect_map=parse_mapping("vat=VAT"),
        bucket_by=["rate"],
        where={"TYPE": "1.1"},
    )
    assert stats.read == 3 and stats.kept == 1 and stats.skipped_missing == 1
    assert cases[0].bucket == "1"
    assert cases[0].inputs == {"net": "100.00", "rate": "1"}
    assert cases[0].expect == {"vat": "24.00"}


def test_corpus_write_read_roundtrip(tmp_path):
    cases = [GoldenCase(id=0, bucket="1", inputs={"net": "1.005"}, expect={"vat": "0.24"})]
    out = tmp_path / "c.jsonl"
    write_corpus(cases, out)
    back = read_corpus(out)
    assert back[0].inputs["net"] == "1.005"  # exact string survives the round-trip


# ---------------------------------------------------------------------------
# rules + audit
# ---------------------------------------------------------------------------


def test_predict_rules_disagree_on_ties():
    base, rate = Decimal("0.50"), Decimal("25")  # raw = 0.125 exactly
    assert predict("half_up", base, rate) == Decimal("0.13")
    assert predict("half_even", base, rate) == Decimal("0.12")
    assert predict("truncate", base, rate) == Decimal("0.12")


# rate 10% over these nets yields raws with exact .xx5 ties (0.005, 0.015, 0.025 …)
# plus non-tie sub-cent digits — enough to separate every rule in RULES.
_TIE_NETS = ("0.05", "0.15", "0.25", "0.35", "0.45", "0.33", "0.37", "1.23", "2.47", "9.99")


def _corpus_for_rule(rule, n=20):
    """Synthetic corpus whose expected VAT was produced by *rule* (Decimal-exact)."""
    cases = []
    for i in range(n):
        net = Decimal(_TIE_NETS[i % len(_TIE_NETS)]) + Decimal(i // len(_TIE_NETS))
        vat = predict(rule, net, Decimal("10"))
        cases.append(GoldenCase(id=i, bucket="10", inputs={"net": str(net), "rate": "10"}, expect={"vat": str(vat)}))
    return cases


def test_audit_recovers_planted_rule():
    report = audit_corpus(_corpus_for_rule("half_even"), "net", "rate", "vat")
    b = report["buckets"][0]
    assert b["rule_match_pct"]["half_even"] == 100.0
    assert b["rule_match_pct"]["half_up"] < 100.0  # ties separate the rules


def test_audit_counts_unexplained_residuals():
    cases = _corpus_for_rule("half_up", n=10)
    # plant a case NO net*rate rule explains (the second-path/gross-inclusive tell)
    cases.append(GoldenCase(id=99, bucket="24", inputs={"net": "10.00", "rate": "24"}, expect={"vat": "2.41"}))
    report = audit_corpus(cases, "net", "rate", "vat")
    assert report["unexplained_by_best_rule"] >= 1
    assert any(r["expected"] == "2.41" for b in report["buckets"] for r in b["residual_examples"])


def test_audit_rate_map_resolves_codes():
    cases = [GoldenCase(id=0, bucket="1", inputs={"net": "100.00", "rate": "1"}, expect={"vat": "24.00"})]
    report = audit_corpus(cases, "net", "rate", "vat", rate_map={"1": "24"})
    assert report["cases_evaluated"] == 1
    assert report["buckets"][0]["best_match_pct"] == 100.0


# ---------------------------------------------------------------------------
# check: rule oracle, runner oracle, liveness
# ---------------------------------------------------------------------------


def test_check_with_rule_passes_and_fails():
    good = check_with_rule(_corpus_for_rule("half_up"), "half_up", "net", "rate", "vat")
    assert good.failed == 0 and good.replayed == good.total
    bad = check_with_rule(_corpus_for_rule("half_up"), "truncate", "net", "rate", "vat")
    assert bad.failed > 0
    assert bad.failures[0]["deltas"]  # names the delta


def test_check_with_runner_roundtrip(tmp_path):
    """External oracle via the one-process JSONL stdio contract."""
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys, json\n"
        "from decimal import Decimal, ROUND_HALF_UP\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line: continue\n"
        "    c = json.loads(line)\n"
        "    vat = (Decimal(c['inputs']['net']) * Decimal(c['inputs']['rate']) / Decimal(100)).quantize(\n"
        "        Decimal('0.01'), ROUND_HALF_UP)\n"
        "    print(json.dumps({'id': c['id'], 'vat': str(vat)}))\n",
        encoding="utf-8",
    )
    result = check_with_runner(_corpus_for_rule("half_up"), [sys.executable, str(runner)])
    assert result.failed == 0 and result.replayed == result.total


def test_check_runner_silence_is_not_success(tmp_path):
    """A runner that emits nothing must yield replayed=0 (the CLI gates on it)."""
    silent = tmp_path / "silent.py"
    silent.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    result = check_with_runner(_corpus_for_rule("half_up"), [sys.executable, str(silent)])
    assert result.replayed == 0 and result.passed == 0


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


def test_cli_extract_audit_check_end_to_end(tmp_path):
    dbf = _dbf_with_cases(
        tmp_path,
        rows=[
            {"NETVALUE": "100.00", "VATCAT": "1", "VATAMT": "24.00", "DOCTYPE": "1.1"},
            {"NETVALUE": "0.50", "VATCAT": "1", "VATAMT": "0.12", "DOCTYPE": "1.1"},  # half_even/truncate tie
        ],
    )
    corpus = tmp_path / "corpus.jsonl"
    runner = CliRunner()
    r1 = runner.invoke(
        calc_golden,
        [
            "extract",
            str(dbf),
            "--inputs",
            "net=NETVALUE,rate=VATCAT",
            "--expect",
            "vat=VATAMT",
            "--bucket-by",
            "rate",
            "--out",
            str(corpus),
        ],
        obj={"json": True},
    )
    assert r1.exit_code == 0, r1.output
    assert json.loads(r1.output)["summary"]["cases"] == 2

    r2 = runner.invoke(
        calc_golden,
        ["audit", str(corpus), "--base", "net", "--rate", "rate", "--target", "vat", "--rate-map", "1=24"],
        obj={"json": True},
    )
    assert r2.exit_code == 0, r2.output
    audit_env = json.loads(r2.output)
    assert audit_env["summary"]["cases_evaluated"] == 2

    # 0.50 * 24% = 0.12 exactly under truncate/half_even; half_up says 0.12 too
    # (0.12 flat) — so half_up passes both cases and the gate is green
    r3 = runner.invoke(
        calc_golden,
        [
            "check",
            str(corpus),
            "--rule",
            "half_even",
            "--base",
            "net",
            "--rate",
            "rate",
            "--target",
            "vat",
            "--rate-map",
            "1=24",
        ],
        obj={"json": True},
    )
    assert r3.exit_code == 0, r3.output
    assert json.loads(r3.output)["summary"]["gate_failed"] is False


def test_cli_check_gate_exit_5_on_breach(tmp_path):
    corpus = tmp_path / "c.jsonl"
    write_corpus([GoldenCase(id=0, bucket="24", inputs={"net": "10.00", "rate": "24"}, expect={"vat": "9.99"})], corpus)
    runner = CliRunner()
    result = runner.invoke(
        calc_golden,
        ["check", str(corpus), "--rule", "half_up", "--base", "net", "--rate", "rate", "--target", "vat"],
        obj={"json": True},
    )
    assert result.exit_code == 5
    env = json.loads(result.output)
    assert env["summary"]["gate_failed"] is True and env["summary"]["failed"] == 1


def test_cli_check_liveness_exit_5_when_nothing_replayed(tmp_path):
    """Non-empty corpus + an oracle that replays nothing => exit 5, never green."""
    corpus = tmp_path / "c.jsonl"
    write_corpus([GoldenCase(id=0, bucket="b", inputs={"x": "1"}, expect={"y": "2"})], corpus)
    runner = CliRunner()
    # rule mode with keys that resolve nothing -> replayed == 0
    result = runner.invoke(
        calc_golden,
        ["check", str(corpus), "--rule", "half_up", "--base", "net", "--rate", "rate", "--target", "vat"],
        obj={"json": True},
    )
    assert result.exit_code == 5
    assert json.loads(result.output)["summary"]["replayed"] == 0


def test_cli_check_requires_exactly_one_oracle(tmp_path):
    corpus = tmp_path / "c.jsonl"
    write_corpus([GoldenCase(id=0, bucket="b", inputs={"x": "1"}, expect={"y": "2"})], corpus)
    runner = CliRunner()
    assert runner.invoke(calc_golden, ["check", str(corpus)], obj={"json": True}).exit_code != 0


def test_to_decimal_exactness():
    assert to_decimal("1.005") == Decimal("1.005")  # no float in the path
    assert to_decimal("garbage") is None
