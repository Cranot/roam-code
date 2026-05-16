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

`dev/example-plugin/` ships three sibling packages so plugin authors
have a copy-fork template for **every** hook on `RoamPluginContext`:

| Package                          | Demonstrates                                       | How to load                                 |
| -------------------------------- | -------------------------------------------------- | ------------------------------------------- |
| `roam_plugin_example/`           | `declare`, `register_framework_detector`, `register_framework_profile`, `register_detector` | entry point (`example`) |
| `roam_plugin_example_extras/`    | `register_command`, `register_bridge`, `register_language_extractor` | env channel (W1292)        |
| `roam_plugin_rails/`             | Dogfood validation of `register_framework_detector` against the real Rails detection rule (W28.2) | env channel             |

Together the three packages cover **all 7 hooks** the typed
`RoamPluginContext` exposes. A real `roam-plugin-nextjs` /
`roam-plugin-laravel` package will pick the subset it needs.

### `roam_plugin_example/` (base)

`roam_plugin_example/__init__.py` registers:

- A no-op framework detector that returns `None` (always defers).
- A tiny detector that returns one synthetic finding when invoked.
- A `FrameworkProfile` bundling the detector with `file_patterns`,
  `recommended_commands`, and `conventions`.
- A `declare()` call so the plugin appears with name + version in
  `roam plugins list`.

### `roam_plugin_example_extras/` (W1292)

`roam_plugin_example_extras/` registers:

- A Click subcommand `roam example-greet --name <name>` via
  `register_command`. Lives in `cli.py` so roam's lazy-import path
  (`module_path` + `attr_name`) works exactly as it does for core's
  `LazyGroup` commands.
- A synthetic `ExampleBridge(LanguageBridge)` via `register_bridge`
  that maps `.example` -> `.example_target` files. Shows the
  `name` / `source_extensions` / `target_extensions` / `detect` /
  `resolve` surface a production bridge fills in.
- An `ExampleExtractor(LanguageExtractor)` via
  `register_language_extractor` keyed on the `.example` extension.
  Demonstrates the `language_name` / `file_extensions` /
  `extract_symbols` / `extract_references` lifecycle plus the
  `_make_symbol(...)` helper on the base class.

Load it via the env channel — it's not declared as an entry point so
consumers (and tests) opt in explicitly:

```bash
PYTHONPATH=dev/example-plugin \
ROAM_PLUGIN_MODULES=roam_plugin_example_extras \
roam plugins list
# Then exercise the new command:
PYTHONPATH=dev/example-plugin \
ROAM_PLUGIN_MODULES=roam_plugin_example_extras \
roam example-greet --name agent
# -> VERDICT: greeted agent
```
