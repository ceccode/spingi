# Use Spingi from your coding agent

Spingi has an MCP server. With it an agent in Claude Code, Codex, OpenCode or any other MCP client can model a place as a scene, write a plan for a robot, check both, run the plan in simulation and read what happened, while you watch the run in the viewer. You talk; the agent writes YAML and calls the tools; the runtime checks and runs.

Everything runs in simulation, on your machine. No robot, no API key, no network call from the server.

## Install

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install "spingi[mcp]"
```

This installs the `spingi` command with the simulator (MuJoCo) and the MCP server. Install it once rather than letting the client run it with `uvx` on every start: the first download is a few hundred megabytes, and a client that waits ten seconds for a server gives up before it ends.

From a clone of the repository, without installing: replace `spingi mcp` in what follows with `uv run --project /path/to/spingi/runtime spingi mcp`.

## Connect your agent

The server is `spingi mcp`. It speaks MCP over standard input and output and works in the folder it is started in: it reads scenes and plans there and writes under `scenes/` and `runs/`. `--root DIR` names another folder.

### Claude Code

```bash
claude mcp add spingi -- spingi mcp
```

Add `--scope project` to write it to `.mcp.json` in the project, for everyone who works on it:

```json
{
  "mcpServers": {
    "spingi": { "command": "spingi", "args": ["mcp"] }
  }
}
```

### Codex

```bash
codex mcp add spingi -- spingi mcp
```

Codex gives a tool 60 seconds by default, and a benchmark of a long plan takes longer. In `~/.codex/config.toml`:

```toml
[mcp_servers.spingi]
command = "spingi"
args = ["mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 600
```

### OpenCode

```bash
opencode mcp add
```

or, in `opencode.json`:

```json
{
  "mcp": {
    "servers": {
      "spingi": { "type": "local", "command": ["spingi", "mcp"], "timeout": 600000 }
    }
  }
}
```

OpenCode's format changed between versions: older ones put the server directly under `mcp` with `"enabled": true`. See [its documentation](https://opencode.ai/docs/mcp-servers/) for the version you run.

### Any other client

A local (stdio) server: command `spingi`, argument `mcp`. Give tools a timeout of a few minutes where the client lets you.

## What to ask

> Patrol the yard with the Go2: check the gauge at the pump and the sign at door C, then come back. Tell me how often it works.

The agent lists the Go2's skills, reads the `yard_outdoor` scene, writes a plan, checks it, runs it gliding and then walking with realistic sensors, and benchmarks it. It ends with a success rate and the `.zip` of a run. Drop that file on [spingi-viewer.netlify.app](https://spingi-viewer.netlify.app) to watch it.

> Here is my warehouse: 8 by 6 metres, two shelves along the north wall with a box on each, a packing bench by the east wall. Make a scene of it and check both boxes are in place.

The agent writes the scene, and the checks tell it what a first draft gets wrong: a misspelled section, a stop that faces away from the shelf, a place inside the bench. It shows you a map to compare with your floor plan, saves the scene under a name, and runs an inspection round in it. A floor plan as an image works too, with an agent that can read images: it measures, you confirm the scale.

## The tools

| Tool | What it does |
|------|--------------|
| `list_robots`, `list_skills` | The robots and what each can do; the skills a plan may use, with their parameters |
| `get_guide` | Short guides: `workflow` (read first), `scene`, `plan`, `landmarks` |
| `list_scenes`, `list_plans`, `read_scene`, `read_plan` | The samples and your own files, and their YAML |
| `describe_scene` | The locations a plan can name, objects, obstacles, landmarks, routes, limits |
| `validate_scene` | Why a file is not a scene, and what a robot would meet in it |
| `preview_scene` | The scene from above as an image, with the warnings in red |
| `save_scene` | Writes `scenes/<name>.yaml` once it is a scene |
| `validate_plan` | What a run would refuse, in the run's own words, and what the plan would meet in the scene |
| `run_plan` | One run in simulation: status, every step with its attempts and reason, timeline, metrics, the `.zip` for the viewer |
| `bench_plan` | Many runs and the sim-to-real gate |
| `get_episode`, `get_events`, `get_episode_frame` | What happened in a run; its event log in pages; a frame the robot's camera took, as an image |

`run_plan` and `bench_plan` take a `mode`: `fake` (instant, logic only), `kinematic` (MuJoCo, the base glides: the default), `physics` (the robot walks, drifts, can fall), `realistic` (walking with drifting odometry, a noisy camera and AprilTags read from frames). A plan is given by name or as YAML text; a scene by name, after `save_scene`.

The guides are also MCP resources (`spingi://guide/scene`) and two prompts, `create_scene` and `plan_task`, start the two conversations above in clients that show prompts.

## What the server will not do

These are decisions, written in [ADR-0030](../adr/0030-agents-use-spingi-through-mcp-in-simulation.md):

- **It does not drive a robot.** Only the in-memory robot and the simulator are reachable. A real robot is run by a person at the operator console.
- **It has no tool that moves the robot outside a plan.** The agent proposes a plan; the runtime validates it and runs it ([ADR-0003](../adr/0003-llm-proposes-runtime-disposes.md)).
- **The model is never the operator.** A step that asks for a person gets the fixed answer the call names, `abort` unless you ask for `skip` or `retry`, and the result counts the requests.
- **It stays in its folder.** Files are read inside the working folder and among the samples, and written under `scenes/` and `runs/`. A path that leaves the folder, also through a link, is refused.
- **It does not call out.** No network, no API key, no telemetry.

## Limits

- A run takes from under a second to a minute or two; twenty walking runs of a long patrol take a few minutes. Raise the client's tool timeout as shown above.
- One run at a time: a second call waits for the first.
- Frames exist only where the machine can render them (an OpenGL context).
- What the simulator cannot model, a scene cannot hold: rotated walls, pits, water, overhangs. See the `scene` guide.
- A scene of a real home or workplace is personal data. It stays on your machine; keep it out of public repositories.
