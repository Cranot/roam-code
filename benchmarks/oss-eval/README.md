# OSS Repository Benchmark Harness (`#37`)

This harness benchmarks `roam` against a local corpus of open-source repositories.

## Files

- `targets.json`: benchmark target manifest (major required repos + local corpus).
- `run_oss_bench.py`: runner that executes `roam` quality commands and writes artifacts.
- `results/latest.json`: latest structured output.
- `results/latest.md`: latest human-readable snapshot.

## Run

```bash
python benchmarks/oss-eval/run_oss_bench.py --timeout-s 30
```

Optional:

- `--init-if-missing`: run `roam init` when `.roam/index.db` is missing.
- `--timeout-s N`: per-command timeout in seconds.

## Notes

- Missing local clones are reported explicitly as `missing_local_repo`.
- `health` is treated as required; missing `dead`/`complexity`/`coupling` is reported as `ok_partial`.
- This design keeps snapshots auditable even when legacy indexes lack full metric tables.
