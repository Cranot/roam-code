# dogfood_security fixture

A small polyglot app (Flask + Laravel + Vue) with intentional, unambiguous
security defects, used by `tests/test_dogfood_security_behavior.py` to
behaviourally exercise roam's security & reachability command cluster on a
REAL indexed repo (copied to a temp dir, then `roam index`).

Intentional defects (ground truth):
- app/web.py       — Flask SQLi (request.args -> cursor.execute) + command
                     injection (request.args -> os.system); imports requests + PyYAML.
- app/pure_dict.py — NO database; pure dict/set ops (effects false-positive probe).
- app/real_db.py   — real sqlite DAO (effects true-positive).
- app/secretsmod.py — Stripe + GitHub tokens (detected); AWS EXAMPLE keys (suppressed).
- routes/api.php + app/Http/Controllers/ReportController.php — Laravel auth gaps.
- resources/js/Widget.vue — Vue v-html sink.
- seeds/generic_vulns.json — CVEs for requests (imported), PyYAML (imported),
                     lodash (declared-not-imported), and a control package.
