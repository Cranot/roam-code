# roam-plugin-example

A minimal reference plugin for `roam-code` showing the substrate
for `roam-plugin-*` packages (framework analyzers — `nextjs`,
`laravel`, `prisma`, `django`, …).

## What a roam plugin is

A roam plugin is any Python package that exposes a
`register(ctx: RoamPluginContext) -> None` callable via the
`roam.plugins` Python entry-point group. At startup, roam:

1. Walks installed packages' metadata for the `roam.plugins` group.
2. Imports each entry-point target.
3. Calls `register(ctx)` to wire the plugin's hooks into roam.

Discovery is wrapped in `try/except` end-to-end — a broken plugin is
recorded as a discovery error (visible via `roam plugins doctor`)
but never crashes roam itself.

## Anatomy

```
roam-plugin-example/
├── pyproject.toml           # entry-point declaration
├── README.md
└── roam_plugin_example/
    └── __init__.py          # exposes register(ctx)
```

The critical line in `pyproject.toml`:

```toml
[project.entry-points."roam.plugins"]
example = "roam_plugin_example:register"
```

The critical line in `roam_plugin_example/__init__.py`:

```python
def register(ctx):
    ctx.declare(name="example", version="0.1.0", description="…")
    ctx.register_framework_detector(my_detector)
    ctx.register_detector("my-task", "my-way", my_detector_fn)
    ctx.register_language_extractor("qml", MyExtractor, extensions=[".qml"])
    ctx.register_bridge(MyBridge())
```

## Available `ctx` methods

| Method                         | Purpose                                          |
| ------------------------------ | ------------------------------------------------ |
| `declare(name, version, ...)`  | Plugin metadata (optional but recommended).      |
| `register_command(...)`        | Add a `roam <name>` CLI subcommand.              |
| `register_detector(...)`       | Add an algorithm-catalog detector.               |
| `register_language_extractor`  | Add a symbol/reference extractor for a language. |
| `register_framework_detector`  | Detect which framework a project uses.           |
| `register_bridge(...)`         | Add a cross-language reference bridge.           |

See `src/roam/plugins/registry.py` in the roam-code repo for the
typed signatures.

## Trying it locally

This package isn't published. To dogfood it against the host repo:

```bash
# Install in editable mode against the host roam-code venv:
pip install -e dev/example-plugin/

# Then ask roam what it sees:
roam plugins              # should list "example"
roam plugins info example
roam plugins doctor       # no errors
```

For one-shot development without installing the package, use the
`ROAM_PLUGIN_MODULES` channel:

```bash
ROAM_PLUGIN_MODULES=roam_plugin_example roam plugins
```

(requires the package directory to be on `PYTHONPATH`).

## What this example does

`roam_plugin_example/__init__.py` registers:

- A no-op framework detector that returns `None` (always defers).
- A tiny detector that returns one synthetic finding when invoked.
- A `declare()` call so the plugin appears with name + version in
  `roam plugins list`.

The example deliberately ships zero CLI commands and zero language
extractors — those are heavier to demonstrate well and would expand
the example into territory the real `roam-plugin-nextjs` /
`roam-plugin-laravel` packages will cover.
