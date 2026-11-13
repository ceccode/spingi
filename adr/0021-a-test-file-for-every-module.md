# 0021: Every module has its own tests

Status: Accepted · Date: 2026-11-13 · Milestone: M4.0

## Context
ADR-0018 and ADR-0020 split the viewer and the simulator adapter into modules, but the tests did not follow. The tests of `timeline.ts`, `describe.ts` and `video.ts` were still in `episode.test.ts`, so a change to one module meant searching another module's tests. The parts `SimAdapter` delegates to (contacts, objects, the head camera, the videos) had no tests of their own: they were covered only through whole runs, where a broken rule shows up as a run that ends differently, far from its cause.

## Decision
- **Viewer: one test file next to each module.** `timeline.test.ts`, `describe.test.ts` and `video.test.ts` hold the tests of their module; `episode.test.ts` keeps reading and checking. `security.test.ts` stays a cross-cutting file for untrusted content.
- **Runtime: each simulator part tested alone.** `tests/sim/test_sim_parts.py` tests `contacts.py`, `SceneObjects`, `HeadCamera` and `Videos` on the lab scene, without running a plan.
- **New modules come with their tests.** A module added from now on gets its test file in the same commit.

## Consequences
- **Same viewer tests, new places.** The viewer still has 73 tests; none was changed, only moved.
- **Eleven runtime tests more** (337 in all). They pin the rules that whole runs only exercised: what counts as an obstacle, where a released object rests, where the robot believes its camera is, the noisy camera's focal length per seed. Breaking any of these rules on purpose fails them.
- **Not covered by unit tests.** The panels and `main.ts` of the viewer, checked by replaying the samples in a headless browser, outside CI.
