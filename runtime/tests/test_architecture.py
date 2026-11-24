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
        "contextlib",
        "difflib",  # the closest known location in a plan error (ADR-0025)
        "__future__",
    }
    for f in CORE.glob("*.py"):
        for mod in _imports(f):
            root = mod.split(".")[0]
            assert root in stdlib_ok or mod.startswith(allowed_prefixes), f"{f}: unexpected import '{mod}'"


def test_the_public_api_does_not_know_its_skins():
    """ADR-0029: the command line and the MCP server are skins over spingi.api, never the other way round."""
    skins = ("spingi.cli", "spingi.mcp_server")
    assert [m for m in _imports(Path("spingi/api.py")) if m.startswith(skins)] == []


def test_nothing_in_the_package_imports_the_command_line():
    offenders = {
        str(f): [m for m in _imports(f) if m.startswith("spingi.cli")]
        for f in Path("spingi").rglob("*.py")
        if f.name != "cli.py"
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_the_mcp_server_knows_spingi_only_through_the_public_api():
    """ADR-0030: a thin skin. What it needs and the API lacks is added to the API, for every other caller too."""
    ours = [m for m in _imports(Path("spingi/mcp_server.py")) if m.split(".")[0] == "spingi"]
    assert set(ours) <= {"spingi", "spingi.api"}, ours
    used = [n for n in ast.walk(ast.parse(Path("spingi/mcp_server.py").read_text())) if isinstance(n, ast.ImportFrom)]
    from_spingi = {a.name for n in used if n.module == "spingi" for a in n.names}
    assert from_spingi == {"__version__", "api"}
