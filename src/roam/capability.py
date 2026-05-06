"""Capability Registry — decorator-driven introspection for roam commands.

redacted redacted. The
architect's correction: the per-command capability YAML must be derived
from decorators, not hand-edited. This module implements the decorator
plus an emitter that walks all registered commands and produces a
machine-readable manifest.

Usage:

    from roam.capability import roam_capability

    @roam_capability(
        category="review",
        summary="Verify a patch against the indexed graph.",
        inputs=["diff_text"],
        outputs=["verdict", "findings"],
        ai_safe=True,
        since="12.0",
    )
    @click.command()
    def critique(...):
        ...

The introspection layer (``CapabilityRegistry``) collects every
``@roam_capability``-decorated callable when it's imported, and
``emit_yaml`` / ``emit_json`` serialises the catalog.

The downstream consumer is the Roam Review GitHub App (Phase 2): it
reads the manifest at startup so it knows which commands are AI-safe to
expose as webhook actions vs which need human approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Capability:
    """One decorated command's metadata."""

    name: str
    category: str
    summary: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    ai_safe: bool = False
    requires_index: bool = True
    since: str | None = None
    deprecated: bool = False
    module: str = ""
    func_name: str = ""


@dataclass
class CapabilityRegistry:
    """Process-wide registry of capability-decorated callables.

    Populated as a side-effect of importing any decorated command module.
    """

    items: dict[str, Capability] = field(default_factory=dict)

    def register(self, cap: Capability) -> None:
        if cap.name in self.items:
            existing = self.items[cap.name]
            if existing.module != cap.module:
                raise ValueError(
                    f"capability name collision: {cap.name!r} already registered from "
                    f"{existing.module!r}; tried to register from {cap.module!r}"
                )
        self.items[cap.name] = cap

    def get(self, name: str) -> Capability | None:
        return self.items.get(name)

    def all(self) -> list[Capability]:
        return sorted(self.items.values(), key=lambda c: (c.category, c.name))

    def by_category(self) -> dict[str, list[Capability]]:
        out: dict[str, list[Capability]] = {}
        for cap in self.all():
            out.setdefault(cap.category, []).append(cap)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_by": "roam.capability.CapabilityRegistry",
            "count": len(self.items),
            "capabilities": [
                {
                    "name": cap.name,
                    "category": cap.category,
                    "summary": cap.summary,
                    "inputs": list(cap.inputs),
                    "outputs": list(cap.outputs),
                    "examples": list(cap.examples),
                    "tags": list(cap.tags),
                    "ai_safe": cap.ai_safe,
                    "requires_index": cap.requires_index,
                    "since": cap.since,
                    "deprecated": cap.deprecated,
                    "module": cap.module,
                    "func_name": cap.func_name,
                }
                for cap in self.all()
            ],
        }


REGISTRY = CapabilityRegistry()


def roam_capability(
    *,
    category: str,
    summary: str,
    name: str | None = None,
    inputs: tuple[str, ...] | list[str] = (),
    outputs: tuple[str, ...] | list[str] = (),
    examples: tuple[str, ...] | list[str] = (),
    tags: tuple[str, ...] | list[str] = (),
    ai_safe: bool = False,
    requires_index: bool = True,
    since: str | None = None,
    deprecated: bool = False,
) -> Callable[[F], F]:
    """Mark a Click command as a capability for introspection.

    Place ABOVE ``@click.command()`` so the decorator wraps a Click
    Command instance — the introspection then has access to the
    Click metadata as well.
    """

    def deco(func: F) -> F:
        cap_name = name or _derive_name(func)
        # For Click Commands, the original module is on the callback
        callback = getattr(func, "callback", None)
        module = getattr(callback, "__module__", "") if callback is not None else getattr(func, "__module__", "")
        func_name = getattr(callback, "__name__", "") if callback is not None else getattr(func, "__name__", "")
        cap = Capability(
            name=cap_name,
            category=category,
            summary=summary,
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            examples=tuple(examples),
            tags=tuple(tags),
            ai_safe=ai_safe,
            requires_index=requires_index,
            since=since,
            deprecated=deprecated,
            module=module,
            func_name=func_name,
        )
        REGISTRY.register(cap)
        # Stash on the function for later introspection (e.g. roam capabilities --explain X)
        setattr(func, "__roam_capability__", cap)
        return func

    return deco


def _derive_name(func: Callable[..., Any]) -> str:
    """Derive the CLI command name from the function or Click Command.

    Order of preference:
      1. ``func.name`` — set by ``@click.command(name=...)``
      2. ``func.__name__`` — set by Python on a function
      3. ``func.callback.__name__`` — Click stores the original here
    Strips a leading ``cmd_`` and converts underscores to dashes.
    """
    candidates: list[str] = []
    name_attr = getattr(func, "name", None)
    if isinstance(name_attr, str) and name_attr:
        candidates.append(name_attr)
    fn_name = getattr(func, "__name__", "")
    if isinstance(fn_name, str) and fn_name:
        candidates.append(fn_name)
    callback = getattr(func, "callback", None)
    if callback is not None:
        cb_name = getattr(callback, "__name__", "")
        if isinstance(cb_name, str) and cb_name:
            candidates.append(cb_name)
    for cand in candidates:
        if cand and cand != "Command":  # skip the click class name
            if cand.startswith("cmd_"):
                cand = cand[4:]
            return cand.replace("_", "-")
    return "<anonymous>"


def emit_yaml() -> str:
    """Emit the capability catalog as YAML using the in-tree minimal emitter.

    Avoids the PyYAML dependency to stay aligned with the rules engine's
    fallback strategy (see roam.rules.engine._emit_simple_yaml).
    """
    data = REGISTRY.to_dict()
    lines: list[str] = [
        f"schema_version: {data['schema_version']}",
        f"generated_by: {data['generated_by']}",
        f"count: {data['count']}",
        "capabilities:",
    ]
    for cap in data["capabilities"]:
        lines.append(f"  - name: {cap['name']}")
        lines.append(f"    category: {cap['category']}")
        lines.append(f"    summary: {_yaml_str(cap['summary'])}")
        if cap["inputs"]:
            lines.append(f"    inputs: [{', '.join(_yaml_str(s) for s in cap['inputs'])}]")
        if cap["outputs"]:
            lines.append(f"    outputs: [{', '.join(_yaml_str(s) for s in cap['outputs'])}]")
        if cap["examples"]:
            lines.append("    examples:")
            for ex in cap["examples"]:
                lines.append(f"      - {_yaml_str(ex)}")
        if cap["tags"]:
            lines.append(f"    tags: [{', '.join(_yaml_str(t) for t in cap['tags'])}]")
        lines.append(f"    ai_safe: {str(cap['ai_safe']).lower()}")
        lines.append(f"    requires_index: {str(cap['requires_index']).lower()}")
        if cap["since"]:
            lines.append(f"    since: {_yaml_str(cap['since'])}")
        if cap["deprecated"]:
            lines.append("    deprecated: true")
        lines.append(f"    module: {cap['module']}")
        lines.append(f"    func_name: {cap['func_name']}")
    return "\n".join(lines) + "\n"


def _yaml_str(s: str) -> str:
    """Quote a string for YAML if needed."""
    if any(c in s for c in (":", "#", "\n", '"', "'", "[", "]", "{", "}", ",", "&", "*", "!", "|", ">")):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s
