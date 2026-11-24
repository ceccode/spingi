"""What a release is made of says the same thing everywhere (ADR-0031): one version, its notes, the registry's
entry, a package README that works as the PyPI page."""

import json
import re
import tomllib
from pathlib import Path

import spingi
from scripts.release_notes import main, section

RUNTIME = Path(__file__).resolve().parents[1]
ROOT = RUNTIME.parent
PROJECT = tomllib.loads((RUNTIME / "pyproject.toml").read_text())["project"]
SERVER = json.loads((ROOT / "server.json").read_text())
README = (RUNTIME / "README.md").read_text()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text()


def test_the_version_is_one_number_everywhere():
    version = PROJECT["version"]
    assert spingi.__version__ == version
    (package,) = SERVER["packages"]
    assert (SERVER["version"], package["version"]) == (version, version)
    (extra,) = package["runtimeArguments"]
    assert extra == {
        "type": "named",
        "name": "--from",
        "value": f"spingi[mcp]=={version}",
        "description": extra["description"],
    }
    locked = re.search(r'\[\[package\]\]\nname = "spingi"\nversion = "([^"]+)"', (RUNTIME / "uv.lock").read_text())
    assert locked and locked.group(1) == version, "run `uv lock` after changing the version"


def test_the_changelog_has_the_notes_of_this_version():
    notes = section(PROJECT["version"], CHANGELOG)
    assert notes and "### Added" in notes, f"CHANGELOG.md needs a section for {PROJECT['version']}"
    assert "\n## " not in notes  # this version's notes and nothing of the next section


def test_release_notes_are_exactly_one_versions_section(capsys):
    text = (
        "# Changelog\n\n## 0.3.0 (2027-01-01)\n\n- new\n\n"
        "## 0.2.0 (2026-11-24)\n\n### Added\n\n- a\n- b\n\n## 0.1.0\n\n- old\n"
    )
    assert section("0.2.0", text) == "### Added\n\n- a\n- b"
    assert section("0.1.0", text) == "- old" and section("0.3.0", text) == "- new"
    assert section("0.2", text) is None and section("9.9.9", text) is None
    assert main(["9.9.9"]) == 1 and "no section for 9.9.9" in capsys.readouterr().err
    assert main([PROJECT["version"]]) == 0 and "### Added" in capsys.readouterr().out


def test_the_registry_entry_keeps_the_registrys_limits():
    """The constraints of the MCP Registry's server.json schema that a careless edit would break."""
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", SERVER["name"]) and 3 <= len(SERVER["name"]) <= 200
    assert SERVER["name"].startswith("io.github.ceccode/")  # GitHub sign-in publishes only under this namespace
    assert 1 <= len(SERVER["description"]) <= 100 and len(SERVER["title"]) <= 100
    (package,) = SERVER["packages"]
    assert (package["registryType"], package["identifier"]) == ("pypi", PROJECT["name"])
    assert package["transport"] == {"type": "stdio"} and package["runtimeHint"] == "uvx"
    assert [a["value"] for a in package["packageArguments"]] == ["mcp"]  # `spingi mcp`
    assert "mcp" in PROJECT["optional-dependencies"]  # the extra the entry asks for exists
    assert SERVER["repository"] == {"url": PROJECT["urls"]["Homepage"], "source": "github", "subfolder": "runtime"}


def test_the_package_readme_proves_the_registry_entry_is_ours():
    """The registry looks for `mcp-name: <server name>` in the README PyPI shows, followed by whitespace or `-->`."""
    assert PROJECT["readme"] == "README.md"
    assert re.search(rf"mcp-name: {re.escape(SERVER['name'])}(\s|-->)", README)


def test_the_package_readme_works_as_the_pypi_page():
    """On PyPI a relative link leads nowhere: links that leave the package are absolute, to the repository."""
    relative = [t for t in re.findall(r"\]\(([^)]+)\)", README) if not t.startswith(("http://", "https://", "#"))]
    assert relative == [], relative
    assert 'uv tool install "spingi[mcp]"' in README
    assert all(url.startswith("https://") for url in PROJECT["urls"].values())
