"""Show temporal coupling: files that change together."""

from __future__ import annotations

import math
import os
import re

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json

# Directories that conventionally hold parallel-translation files; co-change
# between sibling files there is expected, not hidden coupling.
_LOCALE_DIR_TOKENS = frozenset({"locales", "locale", "i18n", "lang", "langs", "translations", "intl", "messages"})
_LOCALE_CODE_RE = re.compile(r"^[a-z]{2,3}(?:[-_][A-Z]{2})?$")
_DOC_DIR_TOKENS = frozenset({"docs", "doc", "documentation", "guide", "guides", "manual"})
_DOC_EXTS = frozenset({".md", ".rst", ".mdx", ".adoc", ".txt"})


def _classify_pair(path_a: str, path_b: str) -> str:
    """Tag known co-change patterns that aren't architectural coupling.

    Returns one of:

    - ``""`` — no special pattern
    - ``"expected_locale"`` — sibling translation files (same dir,
      filenames differ by locale code)
    - ``"expected_doc_hub"`` — sibling docs in a docs/<topic>/ folder
      that always co-change with their cousins
    """
    a = path_a.replace("\\", "/")
    b = path_b.replace("\\", "/")
    dir_a, base_a = os.path.dirname(a), os.path.basename(a)
    dir_b, base_b = os.path.dirname(b), os.path.basename(b)
    if dir_a != dir_b:
        return ""
    parent = os.path.basename(dir_a).lower()

    name_a, ext_a = os.path.splitext(base_a)
    name_b, ext_b = os.path.splitext(base_b)
    if ext_a != ext_b:
        return ""

    # Locale pattern: parent dir is a known i18n root, OR both basenames
    # are short locale codes ("el.ts" + "en.ts"), OR both share a stem
    # with a locale suffix ("strings.el.json" + "strings.en.json").
    looks_locale_dir = parent in _LOCALE_DIR_TOKENS
    if looks_locale_dir or (_LOCALE_CODE_RE.match(name_a) and _LOCALE_CODE_RE.match(name_b)):
        if _LOCALE_CODE_RE.match(name_a) and _LOCALE_CODE_RE.match(name_b):
            return "expected_locale"
        # strings.<lang>.json shape
        a_parts = name_a.rsplit(".", 1)
        b_parts = name_b.rsplit(".", 1)
        if (
            len(a_parts) == 2
            and len(b_parts) == 2
            and a_parts[0] == b_parts[0]
            and _LOCALE_CODE_RE.match(a_parts[1])
            and _LOCALE_CODE_RE.match(b_parts[1])
        ):
            return "expected_locale"

    # Doc-hub: any sibling Markdown/rst pair under a docs/ subtree.
    if ext_a.lower() in _DOC_EXTS:
        path_parts = dir_a.lower().split("/")
        if any(part in _DOC_DIR_TOKENS for part in path_parts):
            return "expected_doc_hub"

    return ""


# ---------------------------------------------------------------------------
# Surprise score: Jaccard similarity against hypergraph patterns
# ---------------------------------------------------------------------------


def _npmi(p_ab, p_a, p_b):
    """Normalized Pointwise Mutual Information.

    NPMI(A,B) = PMI(A,B) / -log(P(A,B))
              = log(P(A,B) / (P(A)*P(B))) / -log(P(A,B))

    Returns a value in [-1, +1]:
      -1 → A and B never co-occur
       0 → statistically independent
      +1 → A and B always co-occur

    This is superior to Jaccard for measuring coupling because it
    accounts for marginal frequencies: two rare files that always
    change together score higher than two ubiquitous files that
    occasionally overlap.

    Reference: Bouma (2009), "Normalized (Pointwise) Mutual Information
    in Collocation Extraction."
    """
    if p_ab <= 0 or p_a <= 0 or p_b <= 0:
        return -1.0
    pmi = math.log(p_ab / (p_a * p_b))
    neg_log_pab = -math.log(p_ab)
    if neg_log_pab == 0:
        return 1.0  # perfect co-occurrence
    return pmi / neg_log_pab


def _compute_surprise(conn, change_fids):
    """Compute surprise score (0-1) for a set of file IDs.

    0.0 = you always change these files together (seen before).
    1.0 = never seen this combination.

    Uses both Jaccard similarity (set overlap) and NPMI (information-
    theoretic) to find the closest historical pattern.
    """
    if not change_fids or len(change_fids) < 2:
        return 0.0, None, 0.0

    change_set = set(change_fids)

    # Get all hyperedge IDs that share at least one member with our change set
    ph = ",".join("?" for _ in change_set)
    candidate_edges = conn.execute(
        f"""SELECT DISTINCT hyperedge_id FROM git_hyperedge_members
            WHERE file_id IN ({ph})""",
        list(change_set),
    ).fetchall()

    if not candidate_edges:
        return 0.5, None, 0.0  # no history → moderate surprise

    max_jaccard = 0.0
    best_pattern = None

    for row in candidate_edges:
        he_id = row["hyperedge_id"]
        members = conn.execute(
            "SELECT file_id FROM git_hyperedge_members WHERE hyperedge_id = ?",
            (he_id,),
        ).fetchall()
        pattern_set = {m["file_id"] for m in members}

        intersection = len(change_set & pattern_set)
        union = len(change_set | pattern_set)
        if union > 0:
            jaccard = intersection / union
            if jaccard > max_jaccard:
                max_jaccard = jaccard
                best_pattern = pattern_set

    # Resolve best pattern paths
    best_paths = None
    if best_pattern:
        ph = ",".join("?" for _ in best_pattern)
        rows = conn.execute(
            f"SELECT path FROM files WHERE id IN ({ph})",
            list(best_pattern),
        ).fetchall()
        best_paths = sorted(r["path"] for r in rows)

    return round(1.0 - max_jaccard, 3), best_paths, round(max_jaccard, 3)


# ---------------------------------------------------------------------------
# Against mode: check coupling for a change set
# ---------------------------------------------------------------------------


def _against_mode(conn, change_fids, file_map, min_strength, min_cochanges):
    """Check which co-change partners are missing from the change set."""
    path_to_id = {}
    file_commits = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        path_to_id[f["path"]] = f["id"]
    id_to_path = {v: k for k, v in path_to_id.items()}
    for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    # Total commits for lift calculation (association rule mining)
    total_commits_row = conn.execute("SELECT COUNT(*) FROM git_commits").fetchone()
    total_commits = max((total_commits_row[0] if total_commits_row else 1), 1)

    change_set = set(change_fids)
    missing = []
    included = []

    # Bulk-fetch every co-change row touching any fid in file_map. The
    # old per-fid SELECT was N+1 in len(file_map). One batched query
    # returns the same rows; we group by which fid in file_map each row
    # touches and walk per-path in Python.
    all_fids = list({fid for fid in file_map.values() if fid is not None})
    partners_by_fid: dict[int, list] = {fid: [] for fid in all_fids}
    if all_fids:
        from roam.db.connection import batched_in

        rows = batched_in(
            conn,
            "SELECT file_id_a, file_id_b, cochange_count "
            "FROM git_cochange "
            "WHERE file_id_a IN ({ph}) OR file_id_b IN ({ph})",
            all_fids,
        )
        in_map = set(all_fids)
        for r in rows:
            # A row might match either side (or both). Attach to every
            # in-map fid the row touches so per-fid iteration below sees
            # the same set of partners as the old per-fid query.
            a, b = r["file_id_a"], r["file_id_b"]
            if a in in_map:
                partners_by_fid.setdefault(a, []).append(r)
            if b in in_map and b != a:
                partners_by_fid.setdefault(b, []).append(r)

    for path, fid in file_map.items():
        partners = partners_by_fid.get(fid, ())

        for p in partners:
            partner_fid = p["file_id_b"] if p["file_id_a"] == fid else p["file_id_a"]
            cochanges = p["cochange_count"]
            if cochanges < min_cochanges:
                continue

            avg = (file_commits.get(fid, 1) + file_commits.get(partner_fid, 1)) / 2
            strength = cochanges / avg if avg > 0 else 0
            if strength < min_strength:
                continue

            # Lift (association rule mining): measures statistical significance
            # of coupling.  lift = P(A,B) / (P(A)*P(B)).
            # lift > 1 → coupling is more than random; lift < 1 → less than random.
            commits_fid = file_commits.get(fid, 1)
            commits_partner = file_commits.get(partner_fid, 1)
            lift = (cochanges * total_commits) / max(commits_fid * commits_partner, 1)

            partner_path = id_to_path.get(partner_fid, f"file_id={partner_fid}")
            entry = {
                "path": partner_path,
                "strength": round(strength, 2),
                "lift": round(lift, 2),
                "cochanges": cochanges,
                "partner_of": path,
            }

            if partner_fid in change_set:
                included.append(entry)
            else:
                missing.append(entry)

    # Deduplicate missing by path (keep highest strength)
    seen = {}
    for m in missing:
        if m["path"] not in seen or m["strength"] > seen[m["path"]]["strength"]:
            seen[m["path"]] = m
    missing = sorted(seen.values(), key=lambda x: -x["strength"])

    return missing, included


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@roam_capability(
    name="coupling",
    category="architecture",
    summary="Show temporal coupling: file pairs that change together",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option(
    "-n",
    "count",
    default=None,
    type=int,
    help=(
        "Number of pairs to show. Default auto-scales by project size: "
        "20 for small projects (<200 files), 50 for mid-size (200-1000), "
        "100 for large (1000+). Round 4 / T: the old fixed default-20 hid "
        "active areas in larger projects."
    ),
)
@click.option("--staged", is_flag=True, help="Check coupling for staged changes")
@click.option(
    "--against",
    "commit_range",
    default=None,
    help="Check coupling for a commit range (e.g. HEAD~3..HEAD)",
)
@click.option(
    "--min-strength",
    default=0.3,
    type=float,
    show_default=True,
    help="Minimum coupling strength for against mode",
)
@click.option(
    "--min-cochanges",
    default=2,
    type=int,
    show_default=True,
    help="Minimum co-change count for against mode",
)
@click.pass_context
def coupling(ctx, count, staged, commit_range, min_strength, min_cochanges):
    """Show temporal coupling: file pairs that change together.

    Unlike ``fn-coupling`` (which tracks symbol-level co-change) and
    ``dark-matter`` (which finds hidden coupling), this command measures
    file-level temporal coupling from git history.

    Default: show top co-change pairs.
    With --staged or --against: show missing co-change partners for your changes.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        # Round 4 / T: auto-scale the default limit by project size so
        # active areas in 1000+ file repos surface in the default
        # output. Explicit -n always wins.
        if count is None:
            try:
                file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 0
            except Exception:
                file_count = 0
            if file_count >= 1000:
                count = 100
            elif file_count >= 200:
                count = 50
            else:
                count = 20

        # --- Against/staged mode ---
        if staged or commit_range:
            root = find_project_root()
            changed = get_changed_files(root, staged=staged, commit_range=commit_range)
            if not changed:
                label = commit_range or "staged"
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "coupling",
                                budget=token_budget,
                                summary={"error": f"No changes for {label}"},
                            )
                        )
                    )
                else:
                    click.echo(f"No changes found for {label}.")
                return

            file_map = resolve_changed_to_db(conn, changed)
            if not file_map:
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "coupling",
                                budget=token_budget,
                                summary={"error": "Changed files not in index"},
                            )
                        )
                    )
                else:
                    click.echo("Changed files not found in index.")
                return

            change_fids = list(file_map.values())
            missing, included = _against_mode(
                conn,
                change_fids,
                file_map,
                min_strength,
                min_cochanges,
            )
            surprise, best_pattern, similarity = _compute_surprise(conn, change_fids)

            if json_mode:
                _against_verdict = "incomplete changeset" if missing else "complete changeset"
                envelope = json_envelope(
                    "coupling",
                    budget=token_budget,
                    summary={
                        "verdict": _against_verdict,
                        "change_set": len(file_map),
                        "missing": len(missing),
                        "surprise_score": surprise,
                    },
                    mode="against",
                    change_set=sorted(file_map.keys()),
                    surprise_score=surprise,
                    closest_similarity=similarity,
                    closest_historical_pattern=best_pattern,
                    missing_cochanges=missing[:30],
                    included_partners=included[:20],
                )
                click.echo(to_json(envelope))
                return

            label = commit_range or "staged"
            click.echo(f"VERDICT: {len(missing)} missing co-change partner(s)\n")
            click.echo(f"=== Coupling Check ({label}, {len(file_map)} files) ===\n")
            click.echo(f"Surprise score: {surprise:.0%}{'  (unfamiliar combination!)' if surprise > 0.7 else ''}")
            click.echo()

            if missing:
                click.echo(f"Missing co-change partners ({len(missing)}):")
                click.echo("(files you usually change together but are not in this diff)")
                rows = []
                for m in missing[:20]:
                    rows.append(
                        [
                            "MISSING",
                            m["path"],
                            f"{m['strength']:.0%}",
                            str(m["cochanges"]),
                            m["partner_of"],
                        ]
                    )
                click.echo(
                    format_table(
                        ["Status", "File", "Strength", "Co-changes", "Partner of"],
                        rows,
                    )
                )
            else:
                click.echo("No missing co-change partners.")

            if included:
                click.echo(f"\nIncluded partners ({len(included)}):")
                rows = []
                for i in included[:10]:
                    rows.append(
                        [
                            "OK",
                            i["path"],
                            f"{i['strength']:.0%}",
                            str(i["cochanges"]),
                        ]
                    )
                click.echo(
                    format_table(
                        ["Status", "File", "Strength", "Co-changes"],
                        rows,
                    )
                )
            return

        # --- Default mode: top co-change pairs ---
        rows = conn.execute(
            """
            SELECT fa.path as path_a, fb.path as path_b,
                   gc.cochange_count
            FROM git_cochange gc
            JOIN files fa ON gc.file_id_a = fa.id
            JOIN files fb ON gc.file_id_b = fb.id
            ORDER BY gc.cochange_count DESC
            LIMIT ?
        """,
            (count,),
        ).fetchall()

        if not rows:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "coupling",
                            budget=token_budget,
                            summary={"verdict": "no significant coupling", "pairs": 0},
                            pairs=[],
                        )
                    )
                )
            else:
                click.echo("VERDICT: no significant coupling\n")
                click.echo("No co-change data available. Run `roam index` on a git repository.")
            return

        # Check which pairs have structural connections (file_edges)
        file_edge_set = set()
        fe_rows = conn.execute(
            "SELECT source_file_id, target_file_id FROM file_edges WHERE symbol_count >= 2"
        ).fetchall()
        for fe in fe_rows:
            file_edge_set.add((fe["source_file_id"], fe["target_file_id"]))
            file_edge_set.add((fe["target_file_id"], fe["source_file_id"]))

        # Build file path -> id lookup and commit counts for normalization
        path_to_id = {}
        file_commits = {}
        for f in conn.execute("SELECT id, path FROM files").fetchall():
            path_to_id[f["path"]] = f["id"]
        for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
            file_commits[fs["file_id"]] = fs["commit_count"] or 1

        # Total commits for lift calculation
        total_commits_row = conn.execute("SELECT COUNT(*) FROM git_commits").fetchone()
        total_commits = max((total_commits_row[0] if total_commits_row else 1), 1)

        table_rows = []
        pair_patterns: list[str] = []
        for r in rows:
            path_a = r["path_a"]
            path_b = r["path_b"]
            cochange = r["cochange_count"]
            fid_a = path_to_id.get(path_a)
            fid_b = path_to_id.get(path_b)

            pattern = _classify_pair(path_a, path_b)
            pair_patterns.append(pattern)
            has_edge = ""
            if fid_a and fid_b:
                if (fid_a, fid_b) in file_edge_set:
                    has_edge = "yes"
                elif pattern:
                    has_edge = "EXPECTED"
                else:
                    has_edge = "HIDDEN"

            strength = ""
            if fid_a and fid_b:
                avg_commits = (file_commits.get(fid_a, 1) + file_commits.get(fid_b, 1)) / 2
                if avg_commits > 0:
                    ratio = cochange / avg_commits
                    strength = f"{ratio:.0%}"

            table_rows.append([str(cochange), strength, has_edge, path_a, path_b])

        if json_mode:
            pairs = []
            for idx, r in enumerate(rows):
                pa, pb = r["path_a"], r["path_b"]
                fid_a, fid_b = path_to_id.get(pa), path_to_id.get(pb)
                has_struct = bool(fid_a and fid_b and (fid_a, fid_b) in file_edge_set)
                strength_val = None
                lift_val = None
                if fid_a and fid_b:
                    avg = (file_commits.get(fid_a, 1) + file_commits.get(fid_b, 1)) / 2
                    if avg > 0:
                        strength_val = round(r["cochange_count"] / avg, 2)
                    # Lift: P(A,B) / (P(A)*P(B)); lift > 1 → statistically significant
                    ca = file_commits.get(fid_a, 1)
                    cb = file_commits.get(fid_b, 1)
                    lift_val = round((r["cochange_count"] * total_commits) / max(ca * cb, 1), 2)
                    # NPMI: information-theoretic coupling strength [-1, +1]
                    # Per Bouma (2009), superior to Jaccard for accounting for
                    # marginal frequencies.
                    p_ab = r["cochange_count"] / total_commits
                    p_a = ca / total_commits
                    p_b = cb / total_commits
                    npmi_val = round(_npmi(p_ab, p_a, p_b), 3)
                else:
                    npmi_val = None
                pattern = pair_patterns[idx] if idx < len(pair_patterns) else _classify_pair(pa, pb)
                pairs.append(
                    {
                        "file_a": pa,
                        "file_b": pb,
                        "cochange_count": r["cochange_count"],
                        "strength": strength_val,
                        "lift": lift_val,
                        "npmi": npmi_val,
                        "has_structural_edge": has_struct,
                        "expected_pattern": pattern or None,
                    }
                )
            hidden_pairs = sum(1 for p in pairs if not p["has_structural_edge"] and not p["expected_pattern"])
            expected_pairs = sum(1 for p in pairs if p["expected_pattern"])
            if pairs:
                top = pairs[0]
                a_base = top["file_a"].replace("\\", "/").rsplit("/", 1)[-1]
                b_base = top["file_b"].replace("\\", "/").rsplit("/", 1)[-1]
                strength_str = f" ({int(top['strength'] * 100)}%)" if top["strength"] else ""
                verdict = f"{len(pairs)} coupled pairs, strongest: {a_base}+{b_base}{strength_str}"
            else:
                verdict = "no significant coupling"
            click.echo(
                to_json(
                    json_envelope(
                        "coupling",
                        budget=token_budget,
                        summary={
                            "verdict": verdict,
                            "pairs": len(pairs),
                            "hidden_coupling": hidden_pairs,
                            "expected_coupling": expected_pairs,
                        },
                        pairs=pairs,
                    )
                )
            )
            return

        # Build verdict for text output
        if table_rows:
            top_row = table_rows[0]
            a_base = rows[0]["path_a"].replace("\\", "/").rsplit("/", 1)[-1]
            b_base = rows[0]["path_b"].replace("\\", "/").rsplit("/", 1)[-1]
            strength_label = f" ({top_row[1]})" if top_row[1] else ""
            verdict_txt = f"{len(table_rows)} coupled pairs, strongest: {a_base}+{b_base}{strength_label}"
        else:
            verdict_txt = "no significant coupling"
        click.echo(f"VERDICT: {verdict_txt}\n")

        click.echo("=== Temporal coupling (co-change frequency) ===")
        click.echo(
            format_table(
                ["co-changes", "strength", "structural?", "file A", "file B"],
                table_rows,
            )
        )

        hidden_count = sum(1 for r in table_rows if r[2] == "HIDDEN")
        expected_count = sum(1 for r in table_rows if r[2] == "EXPECTED")
        total_pairs = len(table_rows)
        if hidden_count:
            pct = hidden_count * 100 / total_pairs if total_pairs else 0
            click.echo(
                f"\n{hidden_count}/{total_pairs} pairs ({pct:.0f}%) have NO import edge but co-change frequently (hidden coupling)."
            )
        if expected_count:
            click.echo(
                f"{expected_count}/{total_pairs} pairs labelled EXPECTED (locale siblings or doc-hub cousins) — not architectural coupling."
            )
