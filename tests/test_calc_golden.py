"""Tests for ``roam calc-golden`` — the golden-master value oracle (L4)."""

from __future__ import annotations

import json
import struct
import sys
from decimal import Decimal

import pytest
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


# ---------------------------------------------------------------------------
# v2: rule families, --derive, era bucketing, runner-payload guard
# ---------------------------------------------------------------------------


def test_rule_families_compute_distinct_shapes():
    from roam.index.golden_calc import predict_family

    net, gross, rate = Decimal("100"), Decimal("113"), Decimal("13")
    # net family: round(100 * 13/100) = 13.00
    assert predict_family("net", "half_up", net, rate) == Decimal("13.00")
    # vat_from_gross: round(113 * 13/113) = round(13.00) = 13.00
    assert predict_family("vat_from_gross", "half_up", gross, rate) == Decimal("13.00")
    # net_from_gross: net'=round(113*100/113)=100.00; vat=113-100=13.00
    assert predict_family("net_from_gross", "half_up", gross, rate) == Decimal("13.00")


def test_apply_derivations_exact_and_marks_derived():
    from roam.index.golden_calc import apply_derivations, parse_derivations

    derivs = parse_derivations("gross=net+vat")
    # net from inputs, vat from expect
    out = apply_derivations({"net": "100.00", "rate": "13"}, {"vat": "13.00"}, derivs)
    assert out is not None
    inputs, derived = out
    assert inputs["gross"] == "113.00"  # Decimal-exact, no float
    assert derived == ("gross",)
    # missing operand -> None (case cannot carry the derived value)
    assert apply_derivations({"net": "100.00"}, {}, derivs) is None


def test_parse_derivations_rejects_bad_forms():
    from roam.index.golden_calc import parse_derivations

    for bad in ("gross=net*vat", "gross=net", "gross=a+b+c"):
        with pytest.raises(ValueError):
            parse_derivations(bad)


def test_era_bucketing_by_index(tmp_path):
    rows = [{"NET": str(i), "CAT": "2", "VAT": "1"} for i in range(25)]
    cases, stats = extract_cases(
        _records(*rows),
        case_map=parse_mapping("net=NET,rate=CAT"),
        expect_map=parse_mapping("vat=VAT"),
        bucket_by=["@index:10"],
    )
    # 25 cases // 10 -> era0 (0-9), era1 (10-19), era2 (20-24)
    assert stats.buckets == {"era0": 10, "era1": 10, "era2": 5}


def test_derived_corpus_roundtrip_and_runner_strip(tmp_path):
    # derived key survives write/read AND is stripped from the runner payload
    c = GoldenCase(
        id=0, bucket="b", inputs={"net": "100", "rate": "13", "gross": "113"}, expect={"vat": "13"}, derived=("gross",)
    )
    out = tmp_path / "c.jsonl"
    write_corpus([c], out)
    back = read_corpus(out)[0]
    assert back.derived == ("gross",)
    # a runner that echoes the keys it received
    echo = tmp_path / "echo.py"
    echo.write_text(
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line: continue\n"
        "    c = json.loads(line)\n"
        "    print(json.dumps({'id': c['id'], 'vat': '13', '_seen': sorted(c['inputs'])}))\n",
        encoding="utf-8",
    )
    result = check_with_runner([back], [sys.executable, str(echo)])
    # the case passes (vat matches) AND gross never reached the runner
    assert result.replayed == 1
    # inspect: re-run capturing the echoed keys
    import subprocess

    payload = json.dumps({"id": 0, "inputs": {k: v for k, v in back.inputs.items() if k not in back.derived}})
    proc = subprocess.run([sys.executable, str(echo)], input=payload + "\n", capture_output=True, text=True)
    assert json.loads(proc.stdout)["_seen"] == ["net", "rate"]  # gross stripped


def test_audit_families_recover_gross_path():
    from roam.index.golden_calc import predict_family

    # build a corpus whose vat was produced by the GROSS path, not net×rate
    cases = []
    for i in range(30):
        net = Decimal("10") + Decimal(i) / Decimal(7)
        rate = Decimal("13")
        vat = predict_family("vat_from_gross", "half_up", net + Decimal("2"), rate)  # gross-based
        gross = str(net + vat)
        cases.append(
            GoldenCase(
                id=i,
                bucket="13",
                inputs={"net": str(net), "rate": "13", "gross": gross},
                expect={"vat": str(vat)},
                derived=("gross",),
            )
        )
    # net-only audit (v1 view) can't fully explain; family audit with gross can
    report = audit_corpus(cases, "net", "rate", "vat", role_keys={"net": "net", "gross": "gross"})
    assert "vat_from_gross" in report["families_fitted"]
    b = report["buckets"][0]
    # the gross family beats the net family here
    net_best = max(v for k, v in b["family_match_pct"].items() if k.startswith("net:"))
    gross_best = max(v for k, v in b["family_match_pct"].items() if k.startswith("vat_from_gross:"))
    assert gross_best >= net_best


def test_audit_backward_compat_net_only():
    # without gross role, audit stays net-family (v1 keys intact)
    cases = _corpus_for_rule("half_up", n=10)
    report = audit_corpus(cases, "net", "rate", "vat")
    assert report["families_fitted"] == ["net"]
    assert "rule_match_pct" in report["buckets"][0]  # v1 key preserved
    assert report["buckets"][0]["rule_match_pct"]["half_up"] == 100.0


# ---------------------------------------------------------------------------
# audit-fix regressions (2026-07-16 adversarial audit)
# ---------------------------------------------------------------------------


def test_runner_partial_answers_fail_the_gate(tmp_path):
    """#1: a runner answering only SOME cases is a runner defect — exit 5."""
    corpus = tmp_path / "c.jsonl"
    write_corpus(_corpus_for_rule("half_up", n=10), corpus)
    partial = tmp_path / "partial.py"
    partial.write_text(
        "import sys, json\n"
        "from decimal import Decimal, ROUND_HALF_UP\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line: continue\n"
        "    c = json.loads(line)\n"
        "    if c['id'] >= 3: continue  # silently drop the rest\n"
        "    vat = (Decimal(c['inputs']['net']) * Decimal(c['inputs']['rate']) / Decimal(100)).quantize(\n"
        "        Decimal('0.01'), ROUND_HALF_UP)\n"
        "    print(json.dumps({'id': c['id'], 'vat': str(vat)}))\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        calc_golden,
        ["check", str(corpus), "--runner", f'"{sys.executable}" "{partial}"'],
        obj={"json": True},
    )
    assert result.exit_code == 5
    env = json.loads(result.output)
    assert env["summary"]["missing"] == 7
    assert env["summary"]["gate_failed"] is True


def test_unrounded_runner_fails_default_tolerance(tmp_path):
    """#2: the old 0.005 default let an UNROUNDED product pass every tie."""
    corpus = tmp_path / "c.jsonl"
    write_corpus([GoldenCase(id=0, bucket="10", inputs={"net": "0.05", "rate": "10"}, expect={"vat": "0.01"})], corpus)
    raw_runner = tmp_path / "raw.py"
    raw_runner.write_text(
        "import sys, json\n"
        "from decimal import Decimal\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line: continue\n"
        "    c = json.loads(line)\n"
        "    raw = Decimal(c['inputs']['net']) * Decimal(c['inputs']['rate']) / Decimal(100)\n"
        "    print(json.dumps({'id': c['id'], 'vat': str(raw)}))  # 0.005, NOT rounded\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        calc_golden,
        ["check", str(corpus), "--runner", f'"{sys.executable}" "{raw_runner}"'],
        obj={"json": True},
    )
    assert result.exit_code == 5  # |0.01 - 0.005| = 0.005 > 0.001 default


def test_dbf_lowercase_mapping_extracts(tmp_path):
    """#3: case-insensitive validation must come with case-insensitive keying."""
    dbf = _dbf_with_cases(tmp_path, rows=[{"NETVALUE": "100.00", "VATCAT": "1", "VATAMT": "24.00", "DOCTYPE": "1.1"}])
    corpus = tmp_path / "c.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        calc_golden,
        ["extract", str(dbf), "--inputs", "net=netvalue", "--expect", "vat=vatamt", "--out", str(corpus)],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["cases"] == 1  # was 0 before the fix


def test_extract_empty_corpus_exits_2(tmp_path):
    """Liveness at the first stage: 0 kept cases from >0 records is a defect."""
    dbf = _dbf_with_cases(tmp_path, rows=[{"NETVALUE": "100.00", "VATCAT": "1", "VATAMT": "24.00", "DOCTYPE": "1.1"}])
    corpus = tmp_path / "c.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        calc_golden,
        [
            "extract",
            str(dbf),
            "--inputs",
            "net=NETVALUE",
            "--expect",
            "vat=VATAMT",
            "--where",
            "DOCTYPE=9.9",  # matches nothing
            "--out",
            str(corpus),
        ],
        obj={"json": True},
    )
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "empty_corpus"


def test_dbf_numeric_overflow_asterisks_are_none(tmp_path):
    """#13: partial '*' padding is dBase overflow — junk, not a value."""
    dbf = _dbf_with_cases(tmp_path, rows=[{"NETVALUE": "**12.34", "VATCAT": "1", "VATAMT": "24.00", "DOCTYPE": "x"}])
    recs = list(iter_dbf_records(dbf))
    assert recs[0]["NETVALUE"] is None


def test_tolerance_parse_is_sealed(tmp_path):
    """#15: a malformed --tolerance must be a clean usage error, not a traceback."""
    corpus = tmp_path / "c.jsonl"
    write_corpus([GoldenCase(id=0, bucket="b", inputs={"x": "1"}, expect={"y": "2"})], corpus)
    runner = CliRunner()
    result = runner.invoke(
        calc_golden,
        [
            "check",
            str(corpus),
            "--rule",
            "half_up",
            "--base",
            "x",
            "--rate",
            "x",
            "--target",
            "y",
            "--tolerance",
            "0,005",
        ],
        obj={"json": True},
    )
    assert result.exit_code == 2
    assert "not a decimal number" in result.output


def test_rule_semantics_bridge_in_summary(tmp_path):
    """#6: the vocabulary bridge rides every audit/check envelope."""
    corpus = tmp_path / "c.jsonl"
    write_corpus(_corpus_for_rule("half_up", n=3), corpus)
    runner = CliRunner()
    env = json.loads(
        runner.invoke(
            calc_golden,
            ["check", str(corpus), "--rule", "half_up", "--base", "net", "--rate", "rate", "--target", "vat"],
            obj={"json": True},
        ).output
    )
    assert env["summary"]["rule_semantics"]["half_up"] == "half_away_from_zero"
