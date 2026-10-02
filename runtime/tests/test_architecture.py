"""ADR-0001 and repo layout (spec, section 11): spingi.core does not import adapters, skills, planner, perception."""

import ast
from pathlib import Path

CORE = Path("spingi/core")
FORBIDDEN = ("spingi.adapters", "spingi.skills", "spingi.planner", "spingi.perception", "rclpy", "rospy")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_has_no_forbidden_imports():
    offenders = {str(f): [m for m in _imports(f) if m.startswith(FORBIDDEN)] for f in CORE.glob("*.py")}
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, f"spingi.core depends on outer layers: {offenders}"


def test_core_third_party_dependencies_are_only_pydantic_and_yaml():
    allowed_prefixes = ("pydantic", "yaml", "spingi.core")
    stdlib_ok = {
        "asyncio",
        "enum",
        "json",
        "math",
        "pathlib",
        "re",
        "time",
        "typing",
        "uuid",
        "collections",
        "__future__",
    }
    for f in CORE.glob("*.py"):
        for mod in _imports(f):
            root = mod.split(".")[0]
            assert root in stdlib_ok or mod.startswith(allowed_prefixes), f"{f}: unexpected import '{mod}'"
