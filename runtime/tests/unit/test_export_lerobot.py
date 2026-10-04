import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pq = pytest.importorskip("pyarrow.parquet")

from spingi.core.human import ScriptedHuman  # noqa: E402
from spingi.export.lerobot import STATE_NAMES, export_lerobot  # noqa: E402
from spingi.session import SessionConfig, run_session  # noqa: E402

RUNNER = Path("plans/demo_material_runner.yaml")
WAREHOUSE = Path("sim/scenes/warehouse_small.yaml")
ROUND = Path("plans/demo_inspection_round.yaml")
LAB = Path("sim/scenes/lab_small.yaml")


async def two_episodes(tmp_path: Path) -> list[Path]:
    a = await run_session(SessionConfig(plan=RUNNER, scene=WAREHOUSE, runs_dir=tmp_path / "runs"), ScriptedHuman())
    b = await run_session(SessionConfig(plan=ROUND, scene=LAB, runs_dir=tmp_path / "runs"), ScriptedHuman())
    return [a.run_dir, b.run_dir]


async def test_export_writes_a_consistent_v3_layout(tmp_path):
    episodes = await two_episodes(tmp_path)
    out = tmp_path / "ds"
    info = export_lerobot(episodes, out, fps=10)
    for rel in (
        "meta/info.json",
        "meta/stats.json",
        "meta/tasks.parquet",
        "meta/episodes/chunk-000/file-000.parquet",
        "data/chunk-000/file-000.parquet",
    ):
        assert (out / rel).exists(), rel
    assert json.loads((out / "meta/info.json").read_text()) == info
    assert info["codebase_version"] == "v3.0" and info["total_episodes"] == 2 and info["total_tasks"] == 2

    data = pq.read_table(out / "data/chunk-000/file-000.parquet").to_pandas()
    assert len(data) == info["total_frames"]
    assert list(data["index"]) == list(range(len(data)))
    assert data["observation.state"].map(len).eq(len(STATE_NAMES)).all()

    eps = pd.read_parquet(out / "meta/episodes/chunk-000/file-000.parquet")
    assert list(eps["dataset_from_index"]) == [0, eps["length"][0]]
    assert eps["dataset_to_index"].iloc[-1] == len(data)
    for _, ep in eps.iterrows():
        frames = data[data["episode_index"] == ep["episode_index"]]
        assert len(frames) == ep["length"] and list(frames["frame_index"]) == list(range(ep["length"]))
        assert frames["next.done"].sum() == 1 and bool(frames["next.done"].iloc[-1])

    tasks = pd.read_parquet(out / "meta/tasks.parquet")
    assert tasks.index.name == "task" and sorted(tasks["task_index"]) == [0, 1]


async def test_gripper_state_follows_pick_and_place(tmp_path):
    episodes = await two_episodes(tmp_path)
    out = tmp_path / "ds"
    export_lerobot(episodes[:1], out)
    data = pq.read_table(out / "data/chunk-000/file-000.parquet").to_pandas()
    gripper = [s[3] for s in data["observation.state"]]
    assert gripper[0] == 0.0 and max(gripper) == 1.0 and gripper[-1] == 0.0
    first, last = gripper.index(1.0), len(gripper) - 1 - gripper[::-1].index(1.0)
    assert all(g == 1.0 for g in gripper[first : last + 1])  # held continuously between pick and place


async def test_action_is_the_next_state(tmp_path):
    episodes = await two_episodes(tmp_path)
    export_lerobot(episodes[1:], tmp_path / "ds")
    data = pq.read_table(tmp_path / "ds/data/chunk-000/file-000.parquet").to_pandas()
    for i in range(len(data) - 1):
        assert list(data["action"].iloc[i]) == pytest.approx(list(data["observation.state"].iloc[i + 1]), abs=1e-5)


def test_export_rejects_paths_that_are_not_episode_folders(tmp_path):
    archive = tmp_path / "r-1.zip"
    archive.write_bytes(b"PK")
    with pytest.raises(ValueError, match="not episode folders"):
        export_lerobot([archive], tmp_path / "ds")
