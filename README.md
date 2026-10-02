# spingi

*Spingi* is Italian for "push!": the word you shout to someone who should keep going. Pronounced *speen-jee*.

A **Physical Agent Runtime** (Python, sim-first) that turns a humanoid robot into a reliable executor of simple physical tasks, and the **Spingi Viewer** (web) that replays what the runtime did. The two projects share a single contract: the Episode format.

This repository is a neutral open-source toolkit. Applications built on top of it and any business material live in separate private repositories; this repo keeps only samples (the demo plan and scenes) that show how to use the system.

Status: **runtime M0 complete, M1 in progress · viewer v0.1 working** · 2026-10-02

## Projects

| Folder | Project | Status |
|--------|---------|--------|
| [runtime/](runtime/README.md) | **Spingi, the Physical Agent Runtime**: executor of simple physical tasks, Python, sim-first, tested on a fake robot and on the G1 in MuJoCo | M0 done, M1 in progress |
| [viewer/](viewer/README.md) | **Episode Viewer**: web replayer for the episodes produced by the runtime, Three.js, static site deployable on Netlify | v0.1: loads a zip, 3D replay of the G1, event timeline |
| [docs/episode-format.md](docs/episode-format.md) | **Episode format**: the contract between the two projects, with JSON schemas in `docs/schemas/` | v0.1, written by the runtime |

## Documents

| File | Audience | Contents |
|------|----------|----------|
| [docs/runtime-spec.md](docs/runtime-spec.md) | Engineering | Runtime specification: principles, architecture, contracts, safety, testing, milestones |
| [docs/episode-format.md](docs/episode-format.md) | Engineering | Episode format: structure, manifest, trajectory, what is missing for v0 |
| [adr/](adr/README.md) | Engineering | Architecture decisions taken (ADR 0001–0006) and the ones still open |

## Quick commands

From the repository root, these delegate to the runtime:

```bash
make setup
```

```bash
make test
```

```bash
make demo-sim-view
```

For the web viewer, from `viewer/`: `npm install` and `npm run dev`.

## Conventions

- Every architectural decision is an ADR; a decision is changed with a new ADR, not by editing the old one.
- The runtime does not know about the viewer and the viewer does not know about the runtime: they talk only through the episode format.

## Next steps

- Viewer: moving objects, two-episode comparison, Netlify deploy.
- Runtime: `warehouse_small` scene with waypoint navigation, operator console (M2), `pick`/`place` on standard containers (M2).
