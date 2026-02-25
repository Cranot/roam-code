# Community Rule Pack

This pack ships 1000+ custom YAML governance rules across `security`, `architecture`, `style`, `correctness`, `performance`, and `dataflow`.

## Usage

- Run directly: `roam rules --rules-dir rules/community`
- Or copy/link into `.roam/rules/` then run `roam check-rules`

Generated rule files: 1001

Language-scoped style pack is generated via:
- `python rules/community/style/generate_language_pack.py`
