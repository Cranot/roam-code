"""Django cross-language bridge: implicit Django relationship resolution.

Resolves cross-references between Django files that are implicit in the
framework but not visible in static imports:
- Admin classes -> Model classes (via @admin.register or admin.site.register)
- Serializer classes -> Model classes (via Meta.model)
- Form classes -> Model classes (via Meta.model)
- FilterSet classes -> Model classes (via Meta.model)
- Signal handlers -> Model classes (via @receiver(signal, sender=Model))
- URL configs -> View classes/functions (via path()/re_path())
- Celery task tagging (via @app.task/@shared_task)

Ported from upstream fork work. See the companion
``src/roam/index/django_post.py`` for transitive inheritance + custom
field resolution that runs after the per-file extraction phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from roam.bridges.base import LanguageBridge
from roam.bridges.registry import register_bridge

# Django project marker filenames
_DJANGO_MARKERS = frozenset(
    {
        "manage.py",
        "admin.py",
        "models.py",
        "settings.py",
        "urls.py",
        "signals.py",
        "serializers.py",
        "forms.py",
        "views.py",
        "tasks.py",
        "filters.py",
    }
)

# --- Admin mechanism regexes ---
_ADMIN_REGISTER_RE = re.compile(r"@admin\.register\((\w+)")
_ADMIN_SITE_REGISTER_RE = re.compile(r"admin\.site\.register\((\w+)")

# --- Meta.model mechanism: base class detection ---
_SERIALIZER_BASES = frozenset({"ModelSerializer", "Serializer", "HyperlinkedModelSerializer"})
_FORM_BASES = frozenset({"ModelForm", "Form"})
_FILTER_BASES = frozenset({"FilterSet"})

# Map base class -> (mechanism, file hint keywords)
_META_MODEL_MECHANISMS: list[tuple[frozenset[str], str, tuple[str, ...]]] = [
    (_SERIALIZER_BASES, "serializes", ("serializer",)),
    (_FORM_BASES, "form_for", ("form",)),
    (_FILTER_BASES, "filters", ("filter",)),
]

# --- Signal handler regexes ---
_RECEIVER_RE = re.compile(r"@receiver\(\s*(\w+)\s*,\s*sender\s*=\s*(\w+)")
_RECEIVER_STR_RE = re.compile(r"@receiver\(\s*(\w+)\s*,\s*sender\s*=\s*['\"](\w+)['\"]")

# --- Celery task regexes ---
_CELERY_TASK_RE = re.compile(r"@(?:app\.task|shared_task|celery_app\.task)")

# --- URL routing regexes ---
_URL_PATH_RE = re.compile(r"(?:path|re_path|url)\(\s*['\"]([^'\"]*)['\"],\s*(\w+(?:\.\w+)*)")
_AS_VIEW_RE = re.compile(r"(\w+)\.as_view\(\)")

# --- include() pattern regex ---
# Matches: include('app.urls'), include('app.urls', namespace='ns'),
#          include(('app.urls', 'app'), namespace='ns')
_INCLUDE_RE = re.compile(
    r"include\(\s*(?:\(?\s*)?['\"]([^'\"]+)['\"]"
    r"(?:.*?namespace\s*=\s*['\"](\w+)['\"])?"
)

# --- DRF router.register() pattern regex ---
# Matches: router.register(r'prefix', ViewSetClass)
#          router.register(r'prefix', ViewSetClass, basename='name')
_DRF_ROUTER_RE = re.compile(r"(?:\w+)\.register\(\s*r?['\"]([^'\"]*)['\"],\s*(\w+)")


@dataclass(frozen=True)
class _IncludeResolutionContext:
    """Inherited state while resolving nested Django include() calls."""

    source_qname: str
    namespace: str | None
    url_prefix: str
    target_files: dict[str, list[dict]]
    symbol_index: dict[str, str]
    depth: int = 0

    def descend(self, namespace: str | None, url_prefix: str) -> _IncludeResolutionContext:
        return _IncludeResolutionContext(
            source_qname=self.source_qname,
            namespace=namespace,
            url_prefix=url_prefix,
            target_files=self.target_files,
            symbol_index=self.symbol_index,
            depth=self.depth + 1,
        )


def _build_model_index(
    target_files: dict[str, list[dict]],
) -> dict[str, tuple[str, bool]]:
    """Build a name -> qualified_name index of model classes in target files.

    Prefers symbols with framework_type='django_model' but also indexes
    plain classes as fallback.
    """
    models: dict[str, tuple[str, bool]] = {}  # name -> (qname, is_django_model)
    for _path, symbols in target_files.items():
        for sym in symbols:
            if sym.get("kind") != "class":
                continue
            name = sym.get("name", "")
            qname = sym.get("qualified_name", name)
            is_django = sym.get("framework_type") == "django_model"
            existing = models.get(name)
            if existing is None or (is_django and not existing[1]):
                models[name] = (qname, is_django)
    return {name: (qname, is_dm) for name, (qname, is_dm) in models.items()}


def _build_symbol_index(
    target_files: dict[str, list[dict]],
) -> dict[str, str]:
    """Build a name -> qualified_name index of all symbols in target files."""
    index: dict[str, str] = {}
    for _path, symbols in target_files.items():
        for sym in symbols:
            name = sym.get("name", "")
            qname = sym.get("qualified_name", name)
            if name not in index:
                index[name] = qname
    return index


def _find_url_file(
    module_path: str,
    target_files: dict[str, list[dict]],
) -> str | None:
    """Map a dotted module path (e.g. 'myapp.urls') to a file key in target_files.

    Converts dots to path separators and checks for a matching key ending
    with the resulting suffix (e.g. 'myapp/urls.py').
    """
    # Convert 'myapp.urls' -> 'myapp/urls.py'
    suffix = module_path.replace(".", "/") + ".py"
    for file_key in target_files:
        # Normalise backslashes for Windows paths
        normalised = file_key.replace("\\", "/")
        if normalised == suffix or normalised.endswith("/" + suffix):
            return file_key
    return None


class _DjangoUrlResolver:
    """Resolve Django URL-config relationships (path()/re_path()/include()).

    Extracted from ``DjangoBridge`` to shrink the large-class surface: the
    URL routing mechanism is the largest self-contained group in the bridge
    — these methods only call each other and use the bridge name (a constant)
    for edge tagging, so they carry no state that belongs on the bridge.
    """

    def __init__(self, bridge_name: str) -> None:
        self._bridge_name = bridge_name

    def resolve_urls(
        self,
        source_path: str,
        source_symbols: list[dict],
        target_files: dict[str, list[dict]],
        symbol_index: dict[str, str],
    ) -> list[dict]:
        edges: list[dict] = []
        path_lower = source_path.lower()
        if "urls" not in path_lower and "url" not in path_lower:
            return edges

        for sym in source_symbols:
            sig = sym.get("signature", "") or ""
            qname = sym.get("qualified_name", sym.get("name", ""))

            # Match path()/re_path()/url() calls
            for m in _URL_PATH_RE.finditer(sig):
                url_pattern = m.group(1)
                view_ref = m.group(2)
                edge = self._make_url_edge(qname, view_ref, url_pattern, symbol_index)
                if edge:
                    edges.append(edge)

            # Match Class.as_view() references
            for m in _AS_VIEW_RE.finditer(sig):
                view_class = m.group(1)
                edge = self._make_url_edge(qname, view_class, "", symbol_index)
                if edge:
                    edges.append(edge)

            # Match include() patterns and resolve recursively
            for m in _INCLUDE_RE.finditer(sig):
                module_path = m.group(1)
                namespace = m.group(2)  # May be None
                # Extract URL prefix from the parent path()/url() wrapping
                prefix_m = _URL_PATH_RE.search(sig)
                url_prefix = prefix_m.group(1) if prefix_m else ""
                include_edges = self._resolve_include(
                    module_path,
                    _IncludeResolutionContext(
                        source_qname=qname,
                        namespace=namespace,
                        url_prefix=url_prefix,
                        target_files=target_files,
                        symbol_index=symbol_index,
                    ),
                )
                edges.extend(include_edges)

        return edges

    def _resolve_include(
        self,
        module_path: str,
        context: _IncludeResolutionContext,
    ) -> list[dict]:
        """Recursively resolve include() patterns to routes_to edges."""
        if context.depth >= 5:
            return []

        url_file = _find_url_file(module_path, context.target_files)
        if url_file is None:
            return []

        return self._resolve_included_url_symbols_preserving_context(
            context.target_files[url_file],
            context,
        )

    def _resolve_included_url_symbols_preserving_context(
        self,
        included_symbols: list[dict],
        context: _IncludeResolutionContext,
    ) -> list[dict]:
        """Resolve included URL symbols while preserving inherited route context."""
        edges: list[dict] = []
        confidence = 0.95 if context.depth == 0 else 0.85

        for sym in included_symbols:
            sig = sym.get("signature", "") or ""
            edges.extend(
                self._route_edges_visible_through_include_context(
                    sig,
                    context,
                    confidence,
                )
            )
            edges.extend(
                self._nested_includes_with_inherited_context(
                    sig,
                    context,
                )
            )

        return edges

    def _route_edges_visible_through_include_context(
        self,
        sig: str,
        context: _IncludeResolutionContext,
        confidence: float,
    ) -> list[dict]:
        """Build route edges whose URL pattern inherits the include prefix."""
        edges: list[dict] = []

        for m in _URL_PATH_RE.finditer(sig):
            child_pattern = m.group(1)
            view_ref = m.group(2)
            view_name = view_ref.rsplit(".", 1)[-1]
            edge = self._make_include_context_route_edge(
                view_name,
                context.url_prefix + child_pattern,
                context,
                confidence,
            )
            if edge:
                edges.append(edge)

        for m in _AS_VIEW_RE.finditer(sig):
            view_class = m.group(1)
            edge = self._make_include_context_route_edge(
                view_class,
                context.url_prefix,
                context,
                confidence,
            )
            if edge:
                edges.append(edge)

        return edges

    def _make_include_context_route_edge(
        self,
        view_name: str,
        url_pattern: str,
        context: _IncludeResolutionContext,
        confidence: float,
    ) -> dict | None:
        """Attach inherited include context to a resolved route target."""
        target_qname = context.symbol_index.get(view_name)
        if target_qname is None:
            return None

        edge: dict = {
            "source": context.source_qname,
            "target": target_qname,
            "kind": "x-lang",
            "bridge": self._bridge_name,
            "mechanism": "routes_to",
            "confidence": confidence,
            "url_pattern": url_pattern,
        }
        if context.namespace:
            edge["namespace"] = context.namespace
        return edge

    def _nested_includes_with_inherited_context(
        self,
        sig: str,
        context: _IncludeResolutionContext,
    ) -> list[dict]:
        """Recurse into nested includes without losing prefix or namespace context."""
        edges: list[dict] = []
        nested_prefix_m = _URL_PATH_RE.search(sig)
        nested_prefix = context.url_prefix + (nested_prefix_m.group(1) if nested_prefix_m else "")

        for m in _INCLUDE_RE.finditer(sig):
            nested_module = m.group(1)
            nested_ns = m.group(2) or context.namespace
            edges.extend(
                self._resolve_include(
                    nested_module,
                    context.descend(nested_ns, nested_prefix),
                )
            )

        return edges

    def _make_url_edge(
        self,
        source_qname: str,
        view_ref: str,
        url_pattern: str,
        symbol_index: dict[str, str],
    ) -> dict | None:
        # Extract the final name from dotted ref (e.g. views.BookView -> BookView)
        view_name = view_ref.rsplit(".", 1)[-1] if "." in view_ref else view_ref
        target_qname = symbol_index.get(view_name)
        if target_qname is None:
            return None
        edge: dict = {
            "source": source_qname,
            "target": target_qname,
            "kind": "x-lang",
            "bridge": self._bridge_name,
            "mechanism": "routes_to",
            "confidence": 0.85,
        }
        if url_pattern:
            edge["url_pattern"] = url_pattern
        return edge


class DjangoBridge(LanguageBridge):
    """Bridge for implicit Django relationships across Python files."""

    # W81 / A6: bumped from inherited "1.0.0" because ``detect()`` tightened
    # from "≥1 Django marker" to "≥2 Django markers" in v12.10.0 (commit
    # 89bd97e0). Edges stamped with the older bridge version were emitted on
    # projects with a single ``models.py`` that no longer activate this
    # bridge; the stamp lets consumers detect that drift and rebuild.
    VERSION: str = "1.1.0"

    @property
    def name(self) -> str:
        return "django"

    @property
    def source_extensions(self) -> frozenset[str]:
        return frozenset({".py"})

    @property
    def target_extensions(self) -> frozenset[str]:
        return frozenset({".py"})

    def detect(self, file_paths: list[str]) -> bool:
        """Return True for a Django-shaped file set.

        A lone ``models.py`` is common in plain Python packages, so require at
        least two Django marker filenames to avoid activating on generic model
        modules.
        """
        markers = set()
        for fp in file_paths:
            # Check if basename matches a Django marker
            basename = fp.rsplit("/", 1)[-1] if "/" in fp else fp
            basename = basename.rsplit("\\", 1)[-1] if "\\" in basename else basename
            if basename in _DJANGO_MARKERS:
                markers.add(basename)
        return len(markers) >= 2

    def resolve(
        self,
        source_path: str,
        source_symbols: list[dict],
        target_files: dict[str, list[dict]],
    ) -> list[dict]:
        """Resolve implicit Django relationships.

        Scans source_symbols for Django patterns (admin registrations,
        Meta.model references, signal receivers, celery decorators, URL
        configs) and matches them against target_files symbols.
        """
        edges: list[dict] = []
        model_index = _build_model_index(target_files)
        symbol_index = _build_symbol_index(target_files)

        edges.extend(self._resolve_admin(source_path, source_symbols, model_index))
        edges.extend(self._resolve_meta_model(source_path, source_symbols, model_index))
        edges.extend(self._resolve_signals(source_symbols, model_index))
        edges.extend(self._resolve_celery(source_symbols))
        url_resolver = _DjangoUrlResolver(self.name)
        edges.extend(url_resolver.resolve_urls(source_path, source_symbols, target_files, symbol_index))
        edges.extend(self._resolve_drf_routers(source_path, source_symbols, target_files, symbol_index))

        return edges

    # ------------------------------------------------------------------
    # Admin mechanism
    # ------------------------------------------------------------------

    def _resolve_admin(
        self,
        source_path: str,
        source_symbols: list[dict],
        model_index: dict[str, tuple[str, bool]],
    ) -> list[dict]:
        edges: list[dict] = []
        for sym in source_symbols:
            if sym.get("kind") == "class":
                edges.extend(self._admin_edges_preserving_symbol_kind(sym, model_index))

        for sym in source_symbols:
            if sym.get("kind") != "class":
                edges.extend(self._admin_edges_preserving_symbol_kind(sym, model_index))

        return edges

    def _admin_edges_preserving_symbol_kind(
        self,
        sym: dict,
        model_index: dict[str, tuple[str, bool]],
    ) -> list[dict]:
        """Return only admin registration edges valid for this symbol kind."""
        sig = sym.get("signature", "") or ""
        qname = sym.get("qualified_name", sym.get("name", ""))
        registration_patterns = (_ADMIN_SITE_REGISTER_RE,)
        if sym.get("kind") == "class":
            registration_patterns = (_ADMIN_REGISTER_RE, _ADMIN_SITE_REGISTER_RE)

        edges: list[dict] = []
        for pattern in registration_patterns:
            for match in pattern.finditer(sig):
                edge = self._make_admin_edge(qname, match.group(1), model_index)
                if edge:
                    edges.append(edge)
        return edges

    def _make_admin_edge(
        self,
        source_qname: str,
        model_name: str,
        model_index: dict[str, tuple[str, bool]],
    ) -> dict | None:
        entry = model_index.get(model_name)
        if entry is None:
            return None
        target_qname, is_django_model = entry
        return {
            "source": source_qname,
            "target": target_qname,
            "kind": "x-lang",
            "bridge": self.name,
            "mechanism": "admin_registers",
            "confidence": 0.9 if is_django_model else 0.7,
        }

    # ------------------------------------------------------------------
    # Meta.model mechanisms (serializes, form_for, filters)
    # ------------------------------------------------------------------

    def _resolve_meta_model(
        self,
        source_path: str,
        source_symbols: list[dict],
        model_index: dict[str, tuple[str, bool]],
    ) -> list[dict]:
        edges: list[dict] = []

        # Build index of child symbols by parent qualified_name prefix
        child_map: dict[str, list[dict]] = {}
        for sym in source_symbols:
            qname = sym.get("qualified_name", "")
            if "." in qname:
                parent_prefix = qname.rsplit(".", 1)[0]
                child_map.setdefault(parent_prefix, []).append(sym)

        for sym in source_symbols:
            if sym.get("kind") != "class":
                continue
            sig = sym.get("signature", "") or ""
            qname = sym.get("qualified_name", sym.get("name", ""))
            path_lower = source_path.lower()

            mechanism = self._detect_meta_mechanism(sig, path_lower)
            if mechanism is None:
                continue

            # Find model reference via child symbols (Meta.model property)
            model_name = self._find_meta_model_name(qname, child_map, source_symbols)
            if model_name is None:
                continue

            entry = model_index.get(model_name)
            if entry is None:
                continue

            target_qname, is_django_model = entry
            # Base class match in signature = 0.9, file path hint only = 0.7
            has_base_match = self._has_base_class_match(sig, mechanism)
            confidence = 0.9 if has_base_match else 0.7

            edges.append(
                {
                    "source": qname,
                    "target": target_qname,
                    "kind": "x-lang",
                    "bridge": self.name,
                    "mechanism": mechanism,
                    "confidence": confidence,
                }
            )

        return edges

    def _detect_meta_mechanism(self, sig: str, path_lower: str) -> str | None:
        """Determine the mechanism type from base class or file path hints."""
        for bases, mechanism, path_hints in _META_MODEL_MECHANISMS:
            for base in bases:
                if base in sig:
                    return mechanism
            for hint in path_hints:
                if hint in path_lower:
                    return mechanism
        return None

    def _has_base_class_match(self, sig: str, mechanism: str) -> bool:
        """Check if the signature has an explicit base class match."""
        for bases, mech, _ in _META_MODEL_MECHANISMS:
            if mech == mechanism:
                return any(base in sig for base in bases)
        return False

    def _find_meta_model_name(
        self,
        class_qname: str,
        child_map: dict[str, list[dict]],
        source_symbols: list[dict],
    ) -> str | None:
        """Find the model name from Meta.model child symbol or meta_model ref."""
        # Check for Meta.model child property
        # Look for children of the class or of Class.Meta
        meta_qname = f"{class_qname}.Meta"
        for prefix in (meta_qname, class_qname):
            for child in child_map.get(prefix, []):
                name = self._model_name_from_meta_child(child)
                if name:
                    return name

        # Fallback: check for meta_model references among source symbols
        for sym in source_symbols:
            if sym.get("kind", "") != "meta_model":
                continue
            if not sym.get("qualified_name", "").startswith(class_qname):
                continue
            target = sym.get("name", "")
            if target:
                return target

        return None

    def _model_name_from_meta_child(self, child: dict) -> str | None:
        """Extract the model class name from a Meta 'model' property child."""
        if child.get("name") != "model" or child.get("kind") != "property":
            return None
        # default_value often holds the model class name
        val = child.get("default_value", "") or ""
        if val:
            return val
        # signature may hold it; extract simple identifier
        child_sig = child.get("signature", "") or ""
        if child_sig:
            m = re.match(r"(\w+)", child_sig)
            if m:
                return m.group(1)
        return None

    # ------------------------------------------------------------------
    # Signal handler mechanism
    # ------------------------------------------------------------------

    def _resolve_signals(
        self,
        source_symbols: list[dict],
        model_index: dict[str, tuple[str, bool]],
    ) -> list[dict]:
        edges: list[dict] = []
        for sym in source_symbols:
            if sym.get("kind") not in ("function", "method"):
                continue
            sig = sym.get("signature", "") or ""
            qname = sym.get("qualified_name", sym.get("name", ""))

            # Try class reference: @receiver(post_save, sender=Model)
            for m in _RECEIVER_RE.finditer(sig):
                signal_name = m.group(1)
                sender_name = m.group(2)
                edge = self._make_signal_edge(qname, sender_name, signal_name, model_index)
                if edge:
                    edges.append(edge)

            # Try string reference: @receiver(post_save, sender='Model')
            for m in _RECEIVER_STR_RE.finditer(sig):
                signal_name = m.group(1)
                sender_name = m.group(2)
                edge = self._make_signal_edge(qname, sender_name, signal_name, model_index)
                if edge:
                    edges.append(edge)

        return edges

    def _make_signal_edge(
        self,
        source_qname: str,
        sender_name: str,
        signal_name: str,
        model_index: dict[str, tuple[str, bool]],
    ) -> dict | None:
        entry = model_index.get(sender_name)
        if entry is None:
            return None
        target_qname, _is_django = entry
        return {
            "source": source_qname,
            "target": target_qname,
            "kind": "x-lang",
            "bridge": self.name,
            "mechanism": "signal_handler",
            "confidence": 0.9,
            "signal": signal_name,
        }

    # ------------------------------------------------------------------
    # Celery task mechanism
    # ------------------------------------------------------------------

    def _resolve_celery(self, source_symbols: list[dict]) -> list[dict]:
        edges: list[dict] = []
        for sym in source_symbols:
            if sym.get("kind") not in ("function", "method"):
                continue
            sig = sym.get("signature", "") or ""
            qname = sym.get("qualified_name", sym.get("name", ""))

            if _CELERY_TASK_RE.search(sig):
                edges.append(
                    {
                        "source": qname,
                        "target": qname,
                        "kind": "x-lang",
                        "bridge": self.name,
                        "mechanism": "celery_task",
                        "confidence": 1.0,
                        "framework_type": "celery_task",
                    }
                )

        return edges

    # ------------------------------------------------------------------
    # DRF router mechanism
    # ------------------------------------------------------------------

    def _resolve_drf_routers(
        self,
        source_path: str,
        source_symbols: list[dict],
        target_files: dict[str, list[dict]],
        symbol_index: dict[str, str],
    ) -> list[dict]:
        """Detect DRF router.register() calls and synthesize routes_to edges."""
        edges: list[dict] = []
        path_lower = source_path.lower()
        if "urls" not in path_lower and "url" not in path_lower:
            return edges

        for sym in source_symbols:
            sig = sym.get("signature", "") or ""
            qname = sym.get("qualified_name", sym.get("name", ""))

            for m in _DRF_ROUTER_RE.finditer(sig):
                prefix = m.group(1)
                viewset_name = m.group(2)
                target_qname = symbol_index.get(viewset_name)
                if target_qname is None:
                    continue

                # Synthesize list and detail routes
                list_pattern = f"{prefix}/" if prefix else "/"
                detail_pattern = f"{prefix}/{{id}}/" if prefix else "/{id}/"

                for url_pattern in (list_pattern, detail_pattern):
                    edges.append(
                        {
                            "source": qname,
                            "target": target_qname,
                            "kind": "x-lang",
                            "bridge": self.name,
                            "mechanism": "routes_to",
                            "confidence": 0.80,
                            "url_pattern": url_pattern,
                        }
                    )

        return edges


# Auto-register on import
register_bridge(DjangoBridge())
