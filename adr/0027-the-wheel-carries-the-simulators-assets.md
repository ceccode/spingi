# 0027: The wheel carries the simulator's assets; samples have names

Status: Accepted · Date: 2026-11-24 · Milestone: A1

## Context
Spingi could only be used from a clone of the repository. The wheel packaged `spingi/` alone, while the robot models, the walking policies, the tag images and the sample scenes sit next to the package, in `runtime/sim/`, and the sample plans in `runtime/plans/`. Three modules found them by walking up from their own file (`Path(__file__).parents[3] / "sim"`), which in an installed package points into `site-packages` and at nothing: `pip install spingi` gave a command that could not simulate a robot. The default scene and every documented command named files relative to `runtime/`, so the command line also depended on the folder it was run in. A tool people cannot install with one line is not adopted, and the MCP server planned next (ADR-0030) is started by a client from an arbitrary folder.

## Decision
- **One module knows where the shipped files are.** `spingi.assets` exposes `MODELS_DIR`, `POLICIES_DIR`, `MARKERS_DIR`, `SCENES_DIR` and `PLANS_DIR`. It uses `spingi/_assets/` when that folder exists, an installed wheel, and the folder next to the package otherwise, a checkout. Both are found from the module's own file, never from the working directory: a folder you run `spingi` in must not supply the robot. `robots.py`, `sim_mujoco/scene.py` and `sim_mujoco/locomotion.py` take their paths from it.
- **The wheel mirrors the checkout.** `pyproject.toml` force-includes `sim/models`, `sim/policies`, `sim/markers`, `sim/scenes` and `plans` under `spingi/_assets/` with the same layout. The repository layout does not change, so the documentation, `NOTICE` and the licensing tests stay as they are. Each model and policy folder travels with its own `LICENSE`.
- **Samples have names.** A reference to a scene or a plan is resolved in steps: a file that exists; `scenes/<name>.yaml` or `plans/<name>.yaml` in the working folder; a sample by its name (`warehouse_small`) or by the path it has in a checkout (`sim/scenes/warehouse_small.yaml`), so a command copied from the documentation runs wherever Spingi is installed. A reference that is none of these ends the command in one line that lists the samples. `spingi scenes` and `spingi plans` list them and `--show NAME` prints one.
- **The generated scene file has a fallback.** MuJoCo resolves `<include>` and `meshdir` from the main file's folder, so the generated MJCF is written next to the robot model and deleted once loaded, as before. Where that folder cannot be written, a read-only install, it goes into a temporary folder that links to the model's files, removed after loading.

## Consequences
- **`spingi run demo_inspection_round --adapter sim` works from an empty folder with the wheel alone.** `make wheel-smoke` builds the wheel, installs it in a clean environment and runs exactly that; a CI job runs it on every push.
- **The wheel grows from a few hundred kilobytes to about 23 MB**, almost all of it the two robots' meshes. PyPI accepts 100 MB per file; the smoke check fails above 60 MB, so growth is noticed before it is a problem.
- **An editable install is unchanged**: it has no `_assets` folder and reads the checkout's files, so nothing is copied into the environment.
- **Reports name a sample by its installed path** when it is not under the working folder. Inside a checkout, `lab_small` resolves to `sim/scenes/lab_small.yaml`, as a person would have typed it.
- **A scene of the working folder shadows a sample of the same name**: `scenes/lab_small.yaml` wins over the shipped `lab_small`. This is what lets people keep their own places next to the samples.
- **12 new tests**: where a checkout and an installed package find their files, the wheel's include list matching the folders the package reads, each resolution step, the one-line error, the read-only fallback loading a model through links, the new commands.
