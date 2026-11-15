"""Every `from app... import x` / `from crawler... import x` in the code must resolve on this branch.

Catches code that depends on a module or function that only exists in another, unmerged PR
(for example onboarding and the pipeline importing app.conversation.store before the
router PR merged). Imports inside functions count too: they are the ones unit tests fake
and therefore never exercise.
"""
import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("app", "crawler")


def _project_imports():
    for pkg in PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 \
                        and node.module.split(".")[0] in PACKAGES:
                    for alias in node.names:
                        yield str(path.relative_to(ROOT)), node.lineno, node.module, alias.name
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in PACKAGES:
                            yield str(path.relative_to(ROOT)), node.lineno, alias.name, None


IMPORTS = list(_project_imports())


def test_found_imports():
    assert len(IMPORTS) > 20


@pytest.mark.parametrize("where,line,module,name", IMPORTS,
                         ids=[f"{w}:{ln}:{m}.{n or ''}" for w, ln, m, n in IMPORTS])
def test_import_resolves(where, line, module, name):
    assert importlib.util.find_spec(module) is not None, f"{where}:{line} imports missing module {module}"
    if name is None or name == "*":
        return
    mod = importlib.import_module(module)
    ok = hasattr(mod, name) or importlib.util.find_spec(f"{module}.{name}") is not None
    assert ok, f"{where}:{line} imports {name!r} from {module}, which does not define it"
