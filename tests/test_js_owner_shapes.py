"""Member calls whose owner the code shows keep their edge (issue 136 repair).

Each shape below was measured losing its true edge when member calls stopped
resolving by name alone. The owner is evidenced in every case: a typed
constructor field, an ``extends`` clause, an ``instanceof`` check, a type
annotation, an exported construction, a barrel re-export, a tsconfig path and
a CommonJS destructuring.
"""

import pytest

from tests.test_js_edge_resolution import calls

FILES = {
    "tsconfig.json": (
        '{"compilerOptions":{"baseUrl":".","paths":{"@lib/*":["src/lib/*"],"@pkg":["./src/pkg/index.ts"]}}}\n'
    ),
    "src/pkg/feature.ts": "export interface Opt {}\nexport function featureFn() { return 1 }\n",
    "src/pkg/index.ts": "export * from './feature'\n",
    "src/alias_bare.ts": "import { featureFn } from '@pkg'\nexport function useAliasBare() {\n  return featureFn()\n}\n",
    "src/lib/factory.ts": "export class Factory {\n  static make() { return 1 }\n}\n",
    "src/alias_member.ts": (
        "import { Factory } from '@lib/factory'\nexport function useFactory() {\n  return Factory.make()\n}\n"
    ),
    "src/lib/deco.ts": "export function Deco() {\n  return (target: any) => target\n}\n",
    "src/decorated.ts": "import { Deco } from '@lib/deco'\n@Deco()\nexport class Decorated {}\n",
    "src/Comp.vue": (
        "<script setup lang=\"ts\">\nimport { featureFn } from './pkg/feature'\nconst value = featureFn()\n</script>\n"
    ),
    "src/feature.test.ts": (
        "import type { Opt } from './pkg/feature'\n"
        "import { featureFn } from './pkg/feature'\n"
        "it('runs', () => {\n  const o: Opt = {}\n  featureFn()\n})\n"
    ),
    "src/svc/app.service.ts": "export class AppService {\n  run() { return 1 }\n}\n",
    "src/di.ts": (
        "import { AppService } from './svc/app.service'\n"
        "export class Controller {\n"
        "  constructor(private readonly svc: AppService) {}\n"
        "  handle() {\n    return this.svc.run()\n  }\n}\n"
    ),
    "src/base.ts": "export class Base {\n  inherited() { return 1 }\n}\n",
    "src/derived.ts": (
        "import { Base } from './base'\n"
        "export class Derived extends Base {\n  go() {\n    return this.inherited()\n  }\n}\n"
    ),
    "src/lib/tools.ts": "export function pathTool() { return 1 }\n",
    "src/paths.ts": "import { pathTool } from '@lib/tools'\nexport function usePaths() {\n  return pathTool()\n}\n",
    "src/a.ts": "export function barrelFn() { return 1 }\n",
    "src/index.ts": "export * from './a'\n",
    "src/barrel.ts": "import { barrelFn } from './index'\nexport function useBarrel() {\n  return barrelFn()\n}\n",
    "src/inst.ts": "class Service {\n  serve() { return 1 }\n}\nexport default new Service()\n",
    "src/useinst.ts": "import svc from './inst'\nexport function useInst() {\n  return svc.serve()\n}\n",
    "src/util.js": "function helper() { return 1 }\nmodule.exports = { helper }\n",
    "src/cjs.js": "const { helper } = require('./util')\nfunction useCjs() {\n  return helper()\n}\nmodule.exports = { useCjs }\n",
    "src/errs.ts": "export class MyError extends Error {\n  code() { return 1 }\n}\n",
    "src/narrow.ts": (
        "import { MyError } from './errs'\n"
        "export function handle(e: unknown) {\n  if (e instanceof MyError) {\n    return e.code()\n  }\n}\n"
    ),
    "src/union.ts": (
        "import { AppService } from './svc/app.service'\n"
        "import { Base } from './base'\n"
        "export function both(x: AppService | Base) {\n"
        "  return x instanceof AppService ? x.run() : x.inherited()\n}\n"
    ),
    # Same member names with no evidenced owner: these must stay unresolved.
    "src/unrelated.ts": (
        "export function guess(x: any, y) {\n  x.run()\n  y.inherited()\n  return x.serve() + y.code()\n}\n"
    ),
}

SHAPES = {
    "injected constructor field": ("src/di.ts", "src/svc/app.service.ts", "AppService.run"),
    "inherited method": ("src/derived.ts", "src/base.ts", "Base.inherited"),
    "instanceof narrowing": ("src/narrow.ts", "src/errs.ts", "MyError.code"),
    "union parameter, first member": ("src/union.ts", "src/svc/app.service.ts", "AppService.run"),
    "union parameter, second member": ("src/union.ts", "src/base.ts", "Base.inherited"),
    "default-exported instance": ("src/useinst.ts", "src/inst.ts", "Service.serve"),
    "barrel re-export": ("src/barrel.ts", "src/a.ts", "barrelFn"),
    "tsconfig path": ("src/paths.ts", "src/lib/tools.ts", "pathTool"),
    "CommonJS destructuring": ("src/cjs.js", "src/util.js", "helper"),
    "exact path alias to a barrel": ("src/alias_bare.ts", "src/pkg/feature.ts", "featureFn"),
    "member through a path alias": ("src/alias_member.ts", "src/lib/factory.ts", "Factory.make"),
    "decorator through a path alias": ("src/decorated.ts", "src/lib/deco.ts", "Deco"),
    "call in a .vue script": ("src/Comp.vue", "src/pkg/feature.ts", "featureFn"),
    "type and value import of one module": ("src/feature.test.ts", "src/pkg/feature.ts", "featureFn"),
}


@pytest.fixture(scope="module")
def shape_calls(tmp_path_factory):
    from tests.conftest import git_init, index_in_process

    root = tmp_path_factory.mktemp("owner-shapes")
    for path, text in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    git_init(root)
    output, code = index_in_process(root)
    assert code == 0, output
    return calls(root)


@pytest.mark.parametrize("shape", list(SHAPES))
def test_evidenced_owner_keeps_its_edge(shape_calls, shape):
    source, target_file, target = SHAPES[shape]
    found = {(e[0], e[2], e[3]) for e in shape_calls}
    assert (source, target_file, target) in found, sorted(e for e in shape_calls if e[0] == source)


def test_unevidenced_receivers_stay_unresolved(shape_calls):
    assert not [e for e in shape_calls if e[0] == "src/unrelated.ts"], shape_calls
