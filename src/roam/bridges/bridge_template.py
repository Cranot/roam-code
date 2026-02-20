"""Template engine cross-language bridge: templates <-> host language symbols.

Resolves cross-references between:
- Jinja2/Django templates and Python view functions
- ERB templates and Ruby controllers
- Handlebars templates and JavaScript/TypeScript code
- Template variable references ({{ var }}) to context dict keys
- Template includes/extends to other template files
"""
from __future__ import annotations

import os
import re

from roam.bridges.base import LanguageBridge
from roam.bridges.registry import register_bridge


# Template file extensions
_TEMPLATE_EXTS = frozenset({".html", ".jinja2", ".jinja", ".j2", ".erb", ".hbs", ".mustache"})

# Host language extensions that render templates
_HOST_EXTS = frozenset({".py", ".rb", ".js", ".ts", ".jsx", ".tsx"})

# --- Template variable extraction patterns ---

# Jinja2/Django: {{ user.name }}, {{ items }}, {% extends "base.html" %}, {% include "header.html" %}
_JINJA_VAR_RE = re.compile(r'\{\{\s*(\w+)(?:\.\w+)*\s*\}\}')
_JINJA_EXTENDS_RE = re.compile(r'\{%\s*extends\s+["\']([^"\']+)["\']\s*%\}')
_JINJA_INCLUDE_RE = re.compile(r'\{%\s*include\s+["\']([^"\']+)["\']\s*%\}')

# ERB: <%= @user.name %>, <%= render partial: "header" %>
_ERB_VAR_RE = re.compile(r'<%=?\s*@?(\w+)(?:\.\w+)*\s*%>')
_ERB_RENDER_RE = re.compile(r'render\s+(?:partial:\s*)?["\']([^"\']+)["\']')

# Handlebars: {{user.name}}, {{> header}}, {{#each items}}
_HBS_VAR_RE = re.compile(r'\{\{(?!>|#|/)(\w+)(?:\.\w+)*\}\}')
_HBS_PARTIAL_RE = re.compile(r'\{\{>\s*(\w+)\s*\}\}')

# --- Host language template rendering patterns ---

# Python: render_template('x.html', user=user), render('x.html', context)
_PY_RENDER_RE = re.compile(
    r'''render(?:_template)?\s*\(\s*['"]([\w./]+\.(?:html|jinja2?|j2))['"]\s*(?:,\s*(.+?))?(?:\)|$)''',
)

# Python keyword args in render_template calls: user=user, items=items
_PY_KWARG_RE = re.compile(r'(\w+)\s*=')

# Express/Handlebars: res.render('template', {user: user})
_JS_RENDER_RE = re.compile(
    r'''(?:res|response)\s*\.\s*render\s*\(\s*['"]([\w./]+)['"]\s*(?:,\s*(\{[^}]*\}))?''',
)


class TemplateBridge(LanguageBridge):
    """Bridge between template files and host language code."""

    @property
    def name(self) -> str:
        return "template"

    @property
    def source_extensions(self) -> frozenset[str]:
        return _TEMPLATE_EXTS

    @property
    def target_extensions(self) -> frozenset[str]:
        return _HOST_EXTS

    def detect(self, file_paths: list[str]) -> bool:
        """Detect if project has template files."""
        for fp in file_paths:
            ext = os.path.splitext(fp)[1].lower()
            if ext in _TEMPLATE_EXTS:
                return True
        return False

    def resolve(self, source_path: str, source_symbols: list[dict],
                target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve template references to host language symbols.

        Strategies:
        1. Template filename matching: render_template('users.html') -> users.html
        2. Variable matching: {{ user }} in template -> user= kwarg in render call
        3. Template includes: {% include "header.html" %} -> header.html file
        """
        edges: list[dict] = []
        source_ext = os.path.splitext(source_path)[1].lower()

        if source_ext not in _TEMPLATE_EXTS:
            return edges

        template_basename = os.path.basename(source_path)

        # Extract template variables from source symbols
        template_vars = self._extract_template_vars(source_symbols, source_ext)
        template_includes = self._extract_template_includes(source_symbols, source_ext)

        # Scan host language files for render calls referencing this template
        for tpath, tsymbols in target_files.items():
            text_ext = os.path.splitext(tpath)[1].lower()
            if text_ext not in _HOST_EXTS:
                continue

            render_info = self._extract_render_calls(tsymbols, text_ext)

            for render_template_name, context_vars, render_sym_name in render_info:
                # Strategy 1: Template filename match
                if self._template_names_match(template_basename, render_template_name):
                    edges.append({
                        "source": template_basename,
                        "target": render_sym_name,
                        "kind": "x-lang",
                        "bridge": self.name,
                        "mechanism": "template-render",
                        "confidence": 0.9,
                    })

                    # Strategy 2: Variable matching
                    for tvar in template_vars:
                        if tvar in context_vars:
                            edges.append({
                                "source": f"{template_basename}:{tvar}",
                                "target": render_sym_name,
                                "kind": "x-lang",
                                "bridge": self.name,
                                "mechanism": "template-var",
                                "confidence": 0.7,
                            })

        # Strategy 3: Template includes -> other template files
        for include_name in template_includes:
            for tpath, tsymbols in target_files.items():
                tbasename = os.path.basename(tpath)
                if self._template_names_match(tbasename, include_name):
                    edges.append({
                        "source": template_basename,
                        "target": tbasename,
                        "kind": "x-lang",
                        "bridge": self.name,
                        "mechanism": "template-include",
                        "confidence": 0.9,
                    })

        return edges

    def _extract_template_vars(self, symbols: list[dict],
                                ext: str) -> set[str]:
        """Extract variable names referenced in template symbols."""
        variables: set[str] = set()
        for sym in symbols:
            name = sym.get("name", "")
            sig = sym.get("signature", "") or ""
            doc = sym.get("docstring", "") or ""
            text = f"{name} {sig} {doc}"

            if ext in (".html", ".jinja2", ".jinja", ".j2"):
                variables.update(m.group(1) for m in _JINJA_VAR_RE.finditer(text))
            elif ext == ".erb":
                variables.update(m.group(1) for m in _ERB_VAR_RE.finditer(text))
            elif ext in (".hbs", ".mustache"):
                variables.update(m.group(1) for m in _HBS_VAR_RE.finditer(text))

        return variables

    def _extract_template_includes(self, symbols: list[dict],
                                    ext: str) -> set[str]:
        """Extract included/extended template names."""
        includes: set[str] = set()
        for sym in symbols:
            sig = sym.get("signature", "") or ""
            doc = sym.get("docstring", "") or ""
            text = f"{sig} {doc}"

            if ext in (".html", ".jinja2", ".jinja", ".j2"):
                includes.update(m.group(1) for m in _JINJA_EXTENDS_RE.finditer(text))
                includes.update(m.group(1) for m in _JINJA_INCLUDE_RE.finditer(text))
            elif ext == ".erb":
                includes.update(m.group(1) for m in _ERB_RENDER_RE.finditer(text))
            elif ext in (".hbs", ".mustache"):
                includes.update(m.group(1) for m in _HBS_PARTIAL_RE.finditer(text))

        return includes

    def _extract_render_calls(self, symbols: list[dict],
                               ext: str) -> list[tuple[str, set[str], str]]:
        """Extract render_template calls from host language symbols.

        Returns list of (template_name, context_var_names, symbol_qualified_name).
        """
        results: list[tuple[str, set[str], str]] = []
        for sym in symbols:
            qname = sym.get("qualified_name", sym.get("name", ""))
            sig = sym.get("signature", "") or ""
            doc = sym.get("docstring", "") or ""
            text = f"{sig} {doc}"

            if ext in (".py",):
                for m in _PY_RENDER_RE.finditer(text):
                    tpl_name = m.group(1)
                    kwargs_str = m.group(2) or ""
                    ctx_vars = set(km.group(1) for km in _PY_KWARG_RE.finditer(kwargs_str))
                    results.append((tpl_name, ctx_vars, qname))
            elif ext in (".js", ".ts", ".jsx", ".tsx"):
                for m in _JS_RENDER_RE.finditer(text):
                    tpl_name = m.group(1)
                    obj_str = m.group(2) or ""
                    # Extract keys from {key: val, ...}
                    ctx_vars = set(re.findall(r'(\w+)\s*:', obj_str))
                    results.append((tpl_name, ctx_vars, qname))

        return results

    def _template_names_match(self, template_basename: str,
                               render_name: str) -> bool:
        """Check if a template file matches a render call reference.

        Handles path variations:
        - Exact: users.html == users.html
        - Path prefix: templates/users.html matches users.html
        - Without extension: users matches users.html
        """
        if template_basename == render_name:
            return True

        # render_name might include path prefix
        if render_name.endswith(template_basename):
            return True

        # Match without extension
        t_stem = os.path.splitext(template_basename)[0]
        r_stem = os.path.splitext(render_name)[0]
        if t_stem == r_stem:
            return True

        # render_name path might end with the template stem
        r_basename = os.path.basename(render_name)
        if r_basename == template_basename:
            return True

        return False


# Auto-register on import
register_bridge(TemplateBridge())
