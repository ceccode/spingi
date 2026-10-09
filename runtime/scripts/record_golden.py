"""Re-records the golden episodes in tests/golden/episodes/ (run after an intended behaviour change).

Each golden episode is a normal run; frames and video are dropped to keep the repository small, since replay
compares behaviour (events and final position), not images.
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from spingi.core.human import ScriptedHuman
from spingi.session import SessionConfig, run_session

OUT = Path(__file__).resolve().parents[1] / "tests" / "golden" / "episodes"

GOLDEN = {
    "material_runner_noisy": (
        SessionConfig(
            plan=Path("plans/demo_material_runner.yaml"),
            scene=Path("sim/scenes/warehouse_small.yaml"),
            adapter="sim",
            perception_noise=0.5,
            seed=7,
        ),
        [],
    ),
    "inspection_round": (
        SessionConfig(
            plan=Path("plans/demo_inspection_round.yaml"), scene=Path("sim/scenes/lab_small.yaml"), adapter="sim"
        ),
        [],
    ),
    "blocked_wall_operator": (
        SessionConfig(
            plan=Path("tests/golden/plans/to_workstation.yaml"),
            scene=Path("sim/scenes/lab_blocked.yaml"),
            adapter="sim",
        ),
        ["retry", "abort"],
    ),
    "inspection_round_go2": (
        SessionConfig(
            plan=Path("plans/demo_inspection_round.yaml"),
            scene=Path("sim/scenes/lab_small.yaml"),
            adapter="sim",
            robot="go2",
        ),
        [],
    ),
}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (cfg, answers) in GOLDEN.items():
        with tempfile.TemporaryDirectory() as tmp:
            cfg.runs_dir = Path(tmp)
            result = await run_session(cfg, ScriptedHuman(responses=answers, default="abort"))
            target = OUT / name
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(result.run_dir, target, ignore=shutil.ignore_patterns("frames", "*.mp4"))
            manifest = json.loads((target / "manifest.json").read_text())
            manifest["files"] = sorted(p.name for p in target.iterdir() if p.name != "manifest.json")
            (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            retries = result.log.count("step.retry")
            print(f"{name}: {result.status}, {result.steps_completed}/{result.steps_total} steps, {retries} retries")


if __name__ == "__main__":
    asyncio.run(main())
