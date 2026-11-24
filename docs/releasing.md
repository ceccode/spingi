# Releasing

A release is a tag ([ADR-0031](../adr/0031-a-release-is-a-tag.md)). Pushing `v0.2.0` makes [the release workflow](../.github/workflows/release.yml) check the package, publish it to PyPI and create the GitHub release with the notes from the changelog. Nobody uploads anything by hand, and no token is stored.

## Once, before the first release

On [pypi.org](https://pypi.org), signed in as the account that will own the project: *Your projects* → *Publishing* → *Add a new pending publisher*, GitHub tab:

| Field | Value |
|-------|-------|
| PyPI project name | `spingi` |
| Owner | `ceccode` |
| Repository name | `spingi` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

In the repository on GitHub: *Settings* → *Environments* → *New environment*, named `pypi`. Adding yourself as a required reviewer there makes every publication wait for a click.

## Every release

1. **Version.** The same number in `runtime/pyproject.toml`, `runtime/spingi/__init__.py` and `server.json` (three places in that file: the server, the package, the `--from` argument). Then `uv lock` in `runtime/`. `tests/test_release.py` fails if they differ.
2. **Changelog.** A section `## <version> (<date>)` in `CHANGELOG.md`, written for people who use Spingi. It becomes the release's notes; the workflow refuses a version without one.
3. **Checks.** In `runtime/`: `make test`, `make lint`, `make wheel-smoke`.
4. **Merge** to `main`, and wait for CI.
5. **Tag** the merged commit and push the tag:

   ```bash
   git tag v0.2.0
   ```

   ```bash
   git push origin v0.2.0
   ```

6. **Watch** the `release` workflow: `check` (tests and the wheel alone), `pypi` (build and publish), `github-release`.
7. **Try it** as a user would, in an empty folder:

   ```bash
   uvx --from "spingi[sim]" spingi run demo_inspection_round --adapter sim --zip
   ```

The READMEs tell people to install from PyPI, so do steps 4 and 5 together: between the merge and the tag, the instructions on `main` point at a version that is not there yet.

A version on PyPI cannot be replaced. If something is wrong, fix it and release the next patch version; `yank` the bad one on PyPI if it is harmful.

## The MCP Registry

The [official MCP Registry](https://modelcontextprotocol.io/registry/quickstart) lists servers so that clients can find them. Spingi's entry is `server.json`; the registry verifies the PyPI package by the `mcp-name:` comment at the top of `runtime/README.md`, which must be on PyPI first. After a release:

```bash
brew install mcp-publisher
```

```bash
mcp-publisher validate
```

```bash
mcp-publisher login github
```

```bash
mcp-publisher publish
```

`validate` checks `server.json` against the registry's schema: run it before the first publication, since the file has been checked here only against the constraints `tests/test_release.py` knows. The entry tells a client to start the server as `uvx --from "spingi[mcp]==<version>" spingi mcp`; try the listing with one client before announcing it. The route this repository tests is the one in [agents.md](agents.md).

## The viewer

The viewer is not released: Netlify deploys `viewer/` from `main` on every push. Its version in `viewer/package.json` changes when the episode format or the viewer's behaviour does.
