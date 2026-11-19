"""Regression coverage for script code sharing Vue SFC tag lines."""

import pytest

from roam.index.parser import _preprocess_vue


@pytest.mark.parametrize(
    "tag,language",
    [
        ('<script lang="ts">', "typescript"),
        ("<script>", "javascript"),
        ('<script setup lang="ts">', "typescript"),
    ],
)
@pytest.mark.parametrize("body", ["export default {}", "const value = compute();\nexport { value };"])
def test_script_body_on_tag_lines_is_preserved(tag, language, body):
    source = f"<template><div>outside</div></template>\n{tag}{body}</script>\n<style>outside</style>"

    processed, actual_language = _preprocess_vue(source.encode())
    text = processed.decode()

    assert text.split() == body.split()
    assert actual_language == language
    assert text.count("\n") == source.count("\n")
    # Retain source line numbers for downstream symbol locations.
    for line_number, line in enumerate(body.splitlines(), start=1):
        assert line in text.split("\n")[line_number]


def test_multiline_script_blocks_keep_both_bodies():
    source = (
        b"<template>outside</template>\n"
        b"<script>\nexport default {};\n</script>\n"
        b'<script setup lang="ts">\nconst value: number = 1;\n</script>\n'
        b"<style>outside</style>"
    )
    processed, language = _preprocess_vue(source)

    assert processed.split() == b"export default {}; const value: number = 1;".split()
    assert language == "typescript"
    assert processed.count(b"\n") == source.count(b"\n")
    assert b"export default {};" in processed.split(b"\n")[2]
    assert b"const value: number = 1;" in processed.split(b"\n")[5]


def test_template_only_has_no_script_code():
    source = b"<template>\n<div>outside</div>\n</template>\n"
    processed, language = _preprocess_vue(source)

    assert not processed.strip()
    assert processed.count(b"\n") == source.count(b"\n")
    assert language == "javascript"
