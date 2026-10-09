# Security

Spingi drives robots. Some bugs are security bugs and some are safety bugs; please report both.

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Please do not open a public issue for a vulnerability or for a way to make the robot move when it should not. Expect an acknowledgement within a few days.

## What is in scope

- **Robot safety**: any path where the runtime can keep a robot moving after an e-stop, a lapsed watchdog, a safety-monitor failure, an exception or a cancellation; any way around the speed cap, the geofence or plan validation.
- **The runtime's inputs**: plan files, scene files, episode folders (`spingi replay`, `spingi export`), the planner model's output, `runtime/.env`, command-line arguments.
- **The viewer**: anything an episode or a `?url=` link can do beyond being displayed as text and 3D (script injection, phishing under the viewer's domain, resource exhaustion).
- **Secrets**: anything that could expose or redirect the Anthropic API key.

## Threat model in short

| Input | Trust | Defences |
|-------|-------|----------|
| Plans and scenes | written by the operator or shared by others | schema validation, safe name alphabet, finite numbers only, no control flow, references limited to dict keys and list indexes, MJCF generated from validated values |
| Planner output | untrusted (a language model) | output constrained by a schema built from the skill whitelist, the same validation as hand-written plans, explicit confirmation before `--run`, world passed as delimited data |
| Episodes | shared between people | parsed as data only; the viewer renders text with `textContent`, refuses archives over 200 MB or expanding beyond 500 MB, fetches only https (http from its own origin), labels external sources; the runtime bounds trajectories on export |
| `runtime/.env` | local file | only the source checkout's file, only allow-listed variables, ignored by git |
| Robot | physical | layered safety (ADR-0011): hardware e-stop, watchdog on every motion command, independent monitor on robot time, terminal e-stop, act-before-log, stop on every abnormal exit |

## Known limits

- The watchdog runs in the same process as the heartbeat. On hardware the robot's own command timeout must be configured as the last line of defence (a rule of the adapter contract since M4.0, implemented by the hardware adapter in M4.1).
- The research robots Spingi targets are not certified machines. Run them in a fenced area with a hardware e-stop; see the safety section of `docs/runtime-spec.md`.
