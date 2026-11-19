"""Call edges retain the scope and physical file of their call sites."""

import os

import pytest

from roam.db.connection import open_db
from tests.conftest import git_init, index_in_process


@pytest.fixture(autouse=True)
def _isolate_git_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")


def test_typescript_call_sources_are_local_and_enclosing(tmp_path):
    files = {
        "storage.ts": "export function useStorage() {}\n",
        "unrelated.ts": "export const data = 42\n",
        "storage.test.ts": (
            "import { useStorage } from './storage'\n"
            "const KEY = 'key'\n"
            "it('string', async () => {\n"
            "  useStorage()\n"
            "})\n"
            "it('shallow', async () => {\n"
            "  const data = useStorage()\n"
            "})\n"
        ),
        "wrappers.ts": (
            "import { useStorage } from './storage'\n"
            "export function useLocalStorage() {\n"
            "  const value = useStorage()\n"
            "  return value\n"
            "}\n"
            "export const useSessionStorage = () => useStorage()\n"
        ),
    }
    for path, source in files.items():
        (tmp_path / path).write_text(source, encoding="utf-8", newline="\n")
    git_init(tmp_path)
    output, status = index_in_process(tmp_path)
    assert status == 0, output

    with open_db(readonly=True, project_root=tmp_path) as conn:
        edges = {
            tuple(row)
            for row in conn.execute(
                """
                SELECT sf.path, s.name, s.kind, origin.path, e.line, tf.path, t.name
                FROM edges e
                JOIN symbols s ON s.id = e.source_id
                JOIN symbols t ON t.id = e.target_id
                JOIN files sf ON sf.id = s.file_id
                JOIN files tf ON tf.id = t.file_id
                JOIN files origin ON origin.id = e.source_file_id
                WHERE e.kind = 'call' AND t.name = 'useStorage'
                """
            )
        }
    target = ("storage.ts", "useStorage")
    assert ("wrappers.ts", "useLocalStorage", "function", "wrappers.ts", 3, *target) in edges
    assert ("wrappers.ts", "useSessionStorage", "function", "wrappers.ts", 6, *target) in edges
    assert ("unrelated.ts", "data", "constant", "storage.test.ts", 7, *target) not in edges
    assert not {e for e in edges if e[3] == "storage.test.ts" and e[1] in {"KEY", "data"}}
    assert edges == {
        ("wrappers.ts", "useLocalStorage", "function", "wrappers.ts", 3, *target),
        ("wrappers.ts", "useSessionStorage", "function", "wrappers.ts", 6, *target),
        ("storage.test.ts", "<module>", "module", "storage.test.ts", 4, *target),
    }


def test_same_line_javascript_and_python_property_keep_lexical_owners(tmp_path):
    (tmp_path / "app.js").write_text(
        "function first() {}\n"
        "function second() {}\n"
        "const x = () => first(), y = () => second();\n"
        "class Box { one() { first(); } two() { second(); } } first();\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "app.py").write_text(
        "def load(): pass\n\nclass Box:\n    @property\n    def value(self):\n        return load()\n",
        encoding="utf-8",
        newline="\n",
    )
    git_init(tmp_path)
    output, status = index_in_process(tmp_path)
    assert status == 0, output

    with open_db(readonly=True, project_root=tmp_path) as conn:
        edges = {
            tuple(row)
            for row in conn.execute(
                """
                SELECT f.path, s.qualified_name, e.line, t.qualified_name
                FROM edges e JOIN symbols s ON s.id = e.source_id
                JOIN symbols t ON t.id = e.target_id
                JOIN files f ON f.id = s.file_id
                WHERE e.kind = 'call' AND t.name IN ('first', 'second', 'load')
                """
            )
        }
    assert ("app.py", "Box.value", 6, "load") in edges
    assert edges == {
        ("app.js", "x", 3, "first"),
        ("app.js", "y", 3, "second"),
        ("app.js", "Box.one", 4, "first"),
        ("app.js", "Box.two", 4, "second"),
        ("app.js", "app.js::<module>", 4, "first"),
        ("app.py", "Box.value", 6, "load"),
    }
