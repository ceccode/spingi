"""The public API on the simulator (ADR-0029): the modes, a run that is cancelled, frames and the map."""

import asyncio
import signal
from pathlib import Path

from PIL import Image

from spingi import api
from spingi.episode import read_manifest

TO_THE_SHELF = {
    "id": "to_the_shelf",
    "steps": [
        {"skill": "navigate", "params": {"to": "shelf_A"}},
        {"skill": "inspect", "params": {"target": "shelf_A", "checks": ["present:red_box"]}},
    ],
}


def test_a_mode_is_how_close_to_the_real_robot_the_run_is(tmp_path):
    kinematic = api.run(TO_THE_SHELF, "lab_small", mode="kinematic", runs_dir=tmp_path / "runs")
    assert kinematic.ok and kinematic.summary.adapter == "sim_mujoco" and kinematic.sim_time_s > 3
    config = read_manifest(Path(kinematic.episode_dir)).config
    assert (config.locomotion, config.odometry, config.perception) == ("kinematic", "truth", "truth")

    physics = api.run(TO_THE_SHELF, "lab_small", mode="physics", robot="go2", runs_dir=tmp_path / "runs")
    walked = read_manifest(Path(physics.episode_dir))
    assert physics.ok and walked.robot.model == "unitree_go2" and walked.config.locomotion == "physics"

    realistic = api.run(TO_THE_SHELF, "lab_small", mode="realistic", runs_dir=tmp_path / "runs")
    config = read_manifest(Path(realistic.episode_dir)).config
    assert (config.locomotion, config.odometry, config.perception, config.camera_noise) == (
        "physics",
        "drift",
        "markers",
        True,
    )


def test_the_frames_the_robot_analysed_can_be_fetched(tmp_path):
    report = api.run(TO_THE_SHELF, "lab_small", mode="kinematic", runs_dir=tmp_path / "runs")
    if not report.summary.frames:  # frames are written only where an OpenGL context is available
        return
    path = api.frame(report.run_id, report.summary.frames[-1], runs_dir=tmp_path / "runs")
    with Image.open(path) as image:
        assert image.width > 300 and image.format in ("PNG", "JPEG")
    inspection = next(e for e in api.events(report.run_id, kinds=["perception"], runs_dir=tmp_path / "runs").events)
    assert inspection["frame_ref"] in report.summary.frames


async def test_cancelling_a_run_stops_the_robot_and_still_writes_the_episode(tmp_path):
    """A program has no Ctrl+C: it cancels the task. The run ends as aborted, as with the stop button."""
    walking = asyncio.Event()

    def on_event(event):
        if event.kind == "skill.start" and event.data.get("skill") == "navigate":
            walking.set()

    before = signal.getsignal(signal.SIGINT)
    run = asyncio.create_task(
        api.arun("demo_inspection_round", mode="kinematic", runs_dir=tmp_path / "runs", on_event=on_event)
    )
    await walking.wait()
    assert signal.getsignal(signal.SIGINT) is before  # no stop button installed: the caller's Ctrl+C stays its own
    await asyncio.sleep(0.05)
    run.cancel()
    report = await run

    assert report.status == "aborted" and 0 < report.steps_completed < report.steps_total
    assert report.summary.timeline[-1].endswith(f"run end: aborted · {report.steps_completed} steps completed")
    assert read_manifest(Path(report.episode_dir)).status == "aborted"
    assert Path(report.archive).is_file()
    last_step = report.summary.steps[-1]
    assert last_step.skill == "navigate" and last_step.outcome == "unfinished"


def test_a_scene_and_a_plan_in_it_are_drawn(tmp_path):
    drawn = api.scene_map("lab_blocked", plan="tests/golden/plans/to_workstation.yaml", out=tmp_path / "map.png")
    assert [w.code for w in drawn.warnings] == ["leg_crosses_obstacle"]
    with Image.open(drawn.path) as image:
        assert image.format == "PNG" and image.width == 1400
    from_text = api.scene_map(text="locations:\n  door: { pose: { x: 1, y: 2 } }\n", root=tmp_path)
    assert from_text.path == str(tmp_path / "scene-map.png") and from_text.warnings == []


def test_bench_on_the_simulator(tmp_path):
    report = api.bench(TO_THE_SHELF, "lab_small", runs=2, mode="kinematic", runs_dir=tmp_path / "runs")
    assert (report.runs, report.adapter, report.locomotion, report.passed) == (2, "sim", "kinematic", True)
    assert report.sim_time_p50_s > 3
