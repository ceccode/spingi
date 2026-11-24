# 0031: A release is a tag: PyPI through trusted publishing, notes from the changelog

Status: Accepted · Date: 2026-11-24 · Milestone: A1

## Context
Spingi had a version number, 0.1.0, that nothing depended on: no tag, no release, no package on PyPI. With the wheel able to simulate on its own (ADR-0027) and an MCP server that clients start from an installed package (ADR-0030), the version becomes what people pin and what a bug report names. A release made by hand is a list of steps someone forgets one of, and an upload token is a secret that sits in a repository's settings until it leaks. The same number also lives in several files, and the MCP Registry adds one more.

## Decision
- **Pushing a tag `v<version>` is the release.** `.github/workflows/release.yml` runs three jobs in order. `check` refuses a tag that is not the package's version or has no section in the changelog, then runs the lint, the tests and the wheel check (`scripts/wheel_smoke.py mcp`). `pypi` builds the distributions and publishes them. `github-release` creates the GitHub release with that version's section of `CHANGELOG.md` as its notes.
- **PyPI trusts the workflow, not a token.** Publication uses trusted publishing (OpenID Connect) from the `pypi` environment. Only the `pypi` job may ask for the publishing identity, and it runs nothing but the build: the code of the package and of its dependencies runs in `check`, which has no such permission.
- **One version, checked.** `tests/test_release.py` holds `pyproject.toml`, `spingi.__version__` and the three versions in `server.json` to the same number, the changelog to a section for it, the package README to the registry's `mcp-name:` marker and to absolute links only, since it is the PyPI page.
- **The MCP Registry entry is published by hand** with `mcp-publisher`, after the package is on PyPI. `server.json` is in the repository and kept in step by the test; putting the publication in the workflow waits until the entry has been tried with clients.
- **The viewer is not versioned by this.** Netlify deploys it from `main`.

## Consequences
- **A release is two commands**, `git tag` and `git push origin <tag>`, after a one-time setup on PyPI and on GitHub ([docs/releasing.md](../docs/releasing.md)). A required reviewer on the `pypi` environment turns publication into an approval.
- **A published version is final.** PyPI does not replace files: a mistake is fixed by the next patch version.
- **The documentation on `main` names the released package.** Between a merge and its tag the install instructions point at a version that does not exist yet: merge and tag together.
- **Actions are pinned to commit SHAs** as in CI, and the workflow uses only the two already used there; the release is created with the runner's `gh`.
- **What is not verified here.** The workflow has not run: it will for the first time on `v0.2.0`. `server.json` follows the registry's published schema but has not been through `mcp-publisher validate`.
- **6 new tests**: the versions agree, the changelog has the version's notes and the script extracts exactly them, `server.json` keeps the registry's limits, the package README carries the marker and no relative link.
