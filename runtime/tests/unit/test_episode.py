import itertools
import json
from pathlib import Path

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import load_plan
from spingi.episode import json_schemas, read_manifest, read_trajectory, write_episode, zip_episode
from spingi.perception.fake import FakePerceiver
from spingi.scenes import load_world
from spingi.skills import default_registry

SCHEMAS_DIR = Path("../docs/schemas")
PLAN = Path("plans/demo_inspection_round.yaml")
SCENE = Path("sim/scenes/lab_small.yaml")


async def run_demo(tmp_path: Path):
    log = EventLog(run_id="r-ep", path=tmp_path / "events.jsonl")
    adapter = FakeAdapter()
    executor = Executor(default_registry(), adapter, FakePerceiver(), ScriptedHuman(), log=log)
    plan = load_plan(PLAN)
    result = await executor.run(plan, load_world(SCENE))
    manifest = write_episode(
        tmp_path,
        log=log,
        plan_path=PLAN,
        scene_path=SCENE,
        plan_id=plan.id,
        steps_total=len(plan.steps),
        status=result.status,
        steps_completed=result.steps_completed,
        adapter=adapter,
        adapter_name="fake",
        robot_model="fake",
    )
    return manifest, log, adapter


async def test_episode_folder_has_every_file(tmp_path):
    manifest, log, adapter = await run_demo(tmp_path)
    for name in ["manifest.json", "scene.yaml", "plan.yaml", "events.jsonl", "trajectory.jsonl"]:
        assert (tmp_path / name).exists(), name
    assert manifest.status == "success" and manifest.steps_completed == 9
    assert set(manifest.files) >= {"scene.yaml", "plan.yaml", "events.jsonl", "trajectory.jsonl"}
    assert read_manifest(tmp_path) == manifest


async def test_trajectory_is_monotonic_and_ends_at_dock(tmp_path):
    await run_demo(tmp_path)
    samples = read_trajectory(tmp_path)
    assert len(samples) >= 2
    assert all(b.t >= a.t for a, b in itertools.pairwise(samples))
    assert abs(samples[-1].robot.x) < 1e-6 and abs(samples[-1].robot.y) < 1e-6


async def test_events_carry_sim_time_aligned_with_trajectory(tmp_path):
    _, log, adapter = await run_demo(tmp_path)
    ends = log.find("skill.end", skill="navigate")
    assert ends and all("sim_t" in e.data for e in ends)
    assert abs(ends[-1].data["sim_t"] - adapter.sim_time_s) < 1e-3  # sim_t is rounded to 3 decimals
    assert all(b.data["sim_t"] >= a.data["sim_t"] for a, b in itertools.pairwise(log.events))


async def test_zip_contains_the_folder(tmp_path):
    await run_demo(tmp_path)
    archive = zip_episode(tmp_path)
    import zipfile

    names = zipfile.ZipFile(archive).namelist()
    assert any(n.endswith("manifest.json") for n in names) and any(n.endswith("trajectory.jsonl") for n in names)


def test_published_schemas_match_the_models():
    """docs/schemas/ is generated from the models: if the models change, regenerate with `make schemas`."""
    for name, schema in json_schemas().items():
        path = SCHEMAS_DIR / name
        assert path.exists(), f"{path} missing: run `make schemas`"
        assert json.loads(path.read_text(encoding="utf-8")) == schema, f"{name} out of date: run `make schemas`"
