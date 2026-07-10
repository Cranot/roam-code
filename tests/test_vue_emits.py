from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from conftest import invoke_cli, parse_json_output


def _run(project, cli_runner):
    return invoke_cli(cli_runner, ["vue-emits"], cwd=project, json_mode=True)


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
                "<template><Child /></template>\n"
                "<script setup>\n"
                "import Child from './Child.vue'\n"
                "</script>\n"
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
            "Child.vue": (
                "<script setup>\n"
                "const emit = defineEmits<{ save: [] }>()\n"
                "emit('save')\n"
                "</script>\n"
            ),
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


def test_vue_dynamic_emit_is_not_flagged(project_factory, cli_runner):
    project = project_factory(
        {
            "Child.vue": (
                "<script setup>\n"
                "const emit = defineEmits<{}>()\n"
                "emit(eventName)\n"
                "</script>\n"
            ),
            "Parent.vue": (
                "<template><Child /></template>\n"
                "<script setup>\n"
                "import Child from './Child.vue'\n"
                "</script>\n"
            ),
        }
    )

    data = parse_json_output(_run(project, cli_runner), "vue-emits")
    assert data["summary"]["finding_count"] == 0
    assert data["findings"] == []
