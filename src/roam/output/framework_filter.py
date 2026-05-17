"""Framework type-alias filter shared across centrality-consuming commands.

Vue's ``computed<T>``, React's ``useState<T>``, and similar framework type
aliases get referenced thousands of times by user code without being
architecturally meaningful. When centrality (PageRank, fan-in) is the
ranking signal, those aliases dominate the "key abstractions" list and
drown the actual domain anchors.

This module exports two pieces:

* ``FRAMEWORK_PRIMITIVE_NAMES`` — the canonical name set, replacing the
  per-command duplicates that used to live in ``cmd_fan``, ``cmd_health``,
  ``cmd_complexity``, and ``cmd_trends``.
* ``is_framework_alias(name, kind, file_path)`` — the predicate used by
  centrality consumers to filter rows before ranking.

Adding a new framework here updates every consumer in one place.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Names of framework primitives that should never count as architecturally
# important regardless of fan-in/fan-out. These tend to appear in type
# aliases (``computed<T>``, ``ref<T>``, ``useState<T>``) referenced
# thousands of times by user code without being meaningful "abstractions".
FRAMEWORK_PRIMITIVE_NAMES = frozenset(
    {
        # Vue 3 Composition API
        "computed",
        "ref",
        "reactive",
        "watch",
        "watchEffect",
        "defineProps",
        "defineEmits",
        "defineExpose",
        "defineSlots",
        "defineModel",
        "onMounted",
        "onUnmounted",
        "onBeforeMount",
        "onBeforeUnmount",
        "onActivated",
        "onDeactivated",
        "onUpdated",
        "onBeforeUpdate",
        "onErrorCaptured",
        "provide",
        "inject",
        "toRef",
        "toRefs",
        "toRaw",
        "unref",
        "isRef",
        "shallowRef",
        "shallowReactive",
        "readonly",
        "shallowReadonly",
        "nextTick",
        "h",
        "resolveComponent",
        "resolveDirective",
        "withDirectives",
        "Suspense",
        "Teleport",
        "KeepAlive",
        "Transition",
        "TransitionGroup",
        "emit",
        "emits",
        "props",
        # React
        "useState",
        "useEffect",
        "useCallback",
        "useMemo",
        "useRef",
        "useContext",
        "useReducer",
        "useLayoutEffect",
        "useImperativeHandle",
        "useDeferredValue",
        "useTransition",
        "useId",
        "useSyncExternalStore",
        # Angular
        "ngOnInit",
        "ngOnDestroy",
        "ngOnChanges",
        "ngAfterViewInit",
        "ngAfterViewChecked",
        "ngAfterContentInit",
        "ngAfterContentChecked",
        "ngDoCheck",
        # Lifecycle / generic
        "constructor",
        "render",
        "toString",
        "valueOf",
        "toJSON",
        "setUp",
        "tearDown",
        "setup",
        "teardown",
        "configure",
        "register",
        "bootstrap",
        "main",
        # i18n shorthand
        "_t",
        "$t",
        "t",
        "i18n",
        # Go
        "init",
        "New",
        "Close",
        "String",
        "Error",
        # Rust
        "new",
        "default",
        "fmt",
        "from",
        "into",
        "drop",
        # Common JS/TS noise
        "exports",
        "module",
        "process",
        "global",
        # Python dunders
        "__init__",
        "__str__",
        "__repr__",
        "__new__",
        "__del__",
        "__enter__",
        "__exit__",
        "__getattr__",
        "__setattr__",
        "__getitem__",
        "__setitem__",
        "__len__",
        "__iter__",
        "__next__",
        "__call__",
        "__hash__",
        "__eq__",
    }
)


# File-path signals that mark generated / type-only declarations whose
# symbols carry inflated centrality. Every user touches them.
TYPE_ONLY_FILE_SUFFIXES = (
    ".d.ts",
    ".types.ts",
    ".types.tsx",
    ".dto.ts",
    ".gen.ts",
    ".pb.ts",
    ".pb.go",
    ".g.ts",
    ".generated.ts",
    ".schema.ts",
)
TYPE_ONLY_DIR_PARTS = frozenset({"types", "type", "schemas", "schema", "generated", "__generated__"})

# Symbol kinds that look "structural" but are usually inert in the call
# graph — properties, type aliases, interfaces. Filtering only fires when
# they also live in a type-only path or carry a framework name.
_TYPE_LIKE_KINDS = frozenset({"prop", "property", "type", "type_alias", "interface", "field"})


def is_framework_alias(name: str | None, kind: str | None = None, file_path: str | None = None) -> bool:
    """Return True when a symbol is likely a framework type alias / hook.

    Three signals fire independently:

    1. Leaf name is a known framework primitive.
    2. File path ends with a generated/type-only suffix.
    3. Kind is property/type/interface AND path lives under a
       ``types/`` / ``schemas/`` directory (these are usually re-exported
       declarations, not call-graph nodes).
    """
    if name:
        leaf = name.rsplit(".", 1)[-1]
        if leaf in FRAMEWORK_PRIMITIVE_NAMES:
            return True

    if file_path:
        normalized = file_path.replace("\\", "/")
        if any(normalized.endswith(suffix) for suffix in TYPE_ONLY_FILE_SUFFIXES):
            return True
        if kind in _TYPE_LIKE_KINDS:
            parts = PurePosixPath(normalized).parts
            if any(part in TYPE_ONLY_DIR_PARTS for part in parts):
                return True

    return False
