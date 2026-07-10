from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from conftest import invoke_cli, parse_json_output


def _run(project, cli_runner):
    return invoke_cli(cli_runner, ["vue-emits"], cwd=project, json_mode=True)


def _run_text(project, cli_runner):
    return invoke_cli(cli_runner, ["vue-emits"], cwd=project, json_mode=False)


def test_vue_child_emit_without_parent_handler_is_reported(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": (
                "<template><button>save</button></template>\n"
                "<script setup>\n"
                "const emit = defineEmits(['save'])\n"
                "function save() { emit('save') }\n"
                "</script>\n"
            ),
            "Parent.vue": (
                "<template><Child /></template>\n<script setup>\nimport Child from './Child.vue'\n</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 1
    assert data["findings"][0]["event"] == "save"
    assert data["findings"][0]["parent"] == "Parent.vue"


def test_vue_child_emit_with_handler_is_clean(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": ("<script setup>\nconst emit = defineEmits<{ save: [] }>()\nemit('save')\n</script>\n"),
            "Parent.vue": (
                '<template><Child @save="onSave" /></template>\n'
                "<script setup>\n"
                "import Child from './Child.vue'\n"
                "const onSave = () => {}\n"
                "</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 0
    assert data["findings"] == []


def test_vue_child_camel_emit_with_kebab_handler_is_clean(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": "<script setup>\nconst emit = defineEmits(['saveItem'])\n</script>\n",
            "Parent.vue": (
                '<template><Child @save-item="onSave" /></template>\n'
                "<script setup>\nimport Child from './Child.vue'\n</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 0
    assert data["findings"] == []
    text_result = _run_text(project, cli_runner)
    assert text_result.exit_code == 0
    assert "VERDICT: No unresolved Vue emitted events" in text_result.output


def test_vue_child_namespaced_camel_emit_with_kebab_handler_is_clean(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": "<script setup>\nconst emit = defineEmits(['update:modelValue'])\n</script>\n",
            "Parent.vue": (
                '<template><Child @update:model-value="onUpdate" /></template>\n'
                "<script setup>\nimport Child from './Child.vue'\n</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 0
    assert data["findings"] == []
    text_result = _run_text(project, cli_runner)
    assert text_result.exit_code == 0
    assert "VERDICT: No unresolved Vue emitted events" in text_result.output


def test_vue_child_camel_emit_with_different_handler_is_reported(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": "<script setup>\nconst emit = defineEmits(['saveItem'])\n</script>\n",
            "Parent.vue": (
                '<template><Child @close="x" /></template>\n'
                "<script setup>\nimport Child from './Child.vue'\n</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 1
    assert [finding["event"] for finding in data["findings"]] == ["saveItem"]
    text_result = _run_text(project, cli_runner)
    assert text_result.exit_code == 0
    assert "emits `saveItem` but this usage has no `@saveItem` handler" in text_result.output


def test_vue_dynamic_emit_is_not_flagged(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": ("<script setup>\nconst emit = defineEmits<{}>()\nemit(eventName)\n</script>\n"),
            "Parent.vue": (
                "<template><Child /></template>\n<script setup>\nimport Child from './Child.vue'\n</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 0
    assert data["findings"] == []
