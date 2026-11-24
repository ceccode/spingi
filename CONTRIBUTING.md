# Contributing to Spingi

Thank you for looking. Spingi drives robots, so the bar for a change is a little higher than in most projects: it must be tested without hardware, and it must not weaken a safety layer. This page says how to get a change in.

## Before you write code

- A bug: open an issue with the command you ran, the plan and the scene (or the episode `.zip`), and what you expected. An episode is the best bug report: it replays what happened.
- A feature: open an issue first and say what you want to do with it. Many ideas fit as a new skill, a new scene or a new adapter without touching the core.
- A security or a safety problem (a way to make the robot move when it should not): do not open a public issue. See [SECURITY.md](SECURITY.md).

## Set up

Requirements: Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node 20+ for the viewer. No GPU, no robot.

```bash
cd runtime && make setup
```

```bash
cd viewer && npm install
```

## Checks before a pull request

The runtime, from `runtime/`:

```bash
make test
```

```bash
make lint
```

```bash
uv run ruff format --check spingi tests scripts
```

The viewer, from `viewer/`, when you changed it:

```bash
npm run lint
```

```bash
npm test
```

```bash
npm run build
```

```bash
npm run e2e
```

CI runs the same commands on every pull request.

## Rules of the house

- **Simulation first.** A feature is born and tested in MuJoCo or on the fake adapter before it reaches a robot. Every component has a test that runs in CI without a GPU and without a robot.
- **The model proposes, the runtime disposes.** A language model or an agent may write a plan. It never gets a way to move the robot that does not go through plan validation ([ADR-0003](adr/0003-llm-proposes-runtime-disposes.md)).
- **Every module has its own tests** ([ADR-0021](adr/0021-a-test-file-for-every-module.md)): a new `spingi/foo.py` comes with `tests/unit/test_foo.py`.
- **Layers.** `spingi.core` imports nothing from adapters, skills, planner or perception; the runtime and the viewer share only the [episode format](docs/episode-format.md). `tests/test_architecture.py` enforces the first rule.
- **A decision is an ADR.** A change that introduces or changes a decision (a new tool, a new structure, a new rule) comes with the next `adr/NNNN-title.md`, written from [adr/template.md](adr/template.md), its row in [adr/README.md](adr/README.md) and the new range in the root README. A bug fix that leaves every decision as it was needs none.
- **Documentation moves with the code.** The READMEs, the [tutorial](docs/tutorial.md) and the [spec](docs/runtime-spec.md) describe what the code does today. A change that makes a sentence false fixes the sentence.
- **Third-party files** (robot models, policies, images) need a license that allows redistribution, the license text next to them and an entry in [NOTICE](NOTICE). `tests/test_licensing.py` checks it.
- **No personal data.** A scene modelled on a real home or workplace, and the episodes recorded in it, do not belong in this repository.

## Commits and pull requests

One change per pull request. Commit messages follow the style of the log: a type, the part of the project, and a sentence that says what is true after the commit, with the ADR when there is one.

```
fix(runtime): an unknown key in a plan is an error, never a default (ADR-0026)
docs(tutorial): from a scene to a patrol, step by step
test(viewer): replay every sample in Chromium, in CI (ADR-0022)
```

Types in use: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`. Parts: `runtime`, `viewer`, `sim`, `perception`, `console`, `tutorial`.

## Releases

A release is a tag; the steps are in [docs/releasing.md](docs/releasing.md). A change that people who use Spingi will notice adds a line to [CHANGELOG.md](CHANGELOG.md).

## License

Spingi is licensed under the [Apache License 2.0](LICENSE). By contributing you agree that your contribution is licensed under the same terms.
