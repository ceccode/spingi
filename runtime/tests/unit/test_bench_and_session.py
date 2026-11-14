import json
from pathlib import Path

import pytest

from spingi.bench import bench, format_report
from spingi.cli import main
from spingi.core.human import ScriptedHuman
from spingi.scenes import load_world
from spingi.session import SessionConfig, run_session

PLAN = Path("plans/demo_material_runner.yaml")
SCENE = Path("sim/scenes/warehouse_small.yaml")


async def test_session_on_fake_adapter_writes_an_episode(tmp_path):
    cfg = SessionConfig(plan=PLAN, scene=SCENE, runs_dir=tmp_path)
    result = await run_session(cfg, ScriptedHuman())
    assert result.ok and result.steps_completed == 8
    for name in ("manifest.json", "events.jsonl", "trajectory.jsonl", "plan.yaml", "scene.yaml"):
        assert (result.run_dir / name).exists()
    assert result.log.count("safety.armed") == 1


async def test_bench_without_noise_passes(tmp_path):
    report, runs = await bench(SessionConfig(plan=PLAN, scene=SCENE), 10, out_dir=tmp_path)
    assert report.passed and report.runs == 10 and len(runs) == 10
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["passed"] is True and (tmp_path / "runs.jsonl").read_text().count("\n") == 10
    assert "GATE PASSED" in format_report(report)


async def test_bench_with_heavy_noise_fails_and_counts_operator_requests(tmp_path):
    report, runs = await bench(
        SessionConfig(plan=PLAN, scene=SCENE, perception_noise=0.9), 10, out_dir=tmp_path, keep_failed_episodes=True
    )
    assert not report.passed and report.success_rate < 0.5
    assert report.needs_human_per_100 == 100 * sum(r.human_requests for r in runs) / 10
    assert any((tmp_path / "failed").iterdir())
    assert "GATE FAILED" in format_report(report)


async def test_bench_is_reproducible_with_the_same_seed():
    cfg = SessionConfig(plan=PLAN, scene=SCENE, perception_noise=0.5, seed=7)
    a, _ = await bench(cfg, 8)
    b, _ = await bench(cfg, 8)
    assert a.success_rate == b.success_rate and a.retries_per_run == b.retries_per_run


def test_cli_bench_gate_sets_the_exit_code(tmp_path, capsys):
    base = ["bench", str(PLAN), "--scene", str(SCENE), "--runs", "5", "--runs-dir", str(tmp_path), "--gate"]
    assert main(base) == 0
    assert main([*base, "--noise", "0.95"]) == 1
    assert "GATE FAILED" in capsys.readouterr().out


@pytest.mark.parametrize(
    "settings", [{"odometry": "drift"}, {"camera_noise": True}, {"record_camera": True}, {"record_video": True}]
)
def test_simulated_sensors_and_videos_need_the_simulator(tmp_path, settings):
    with pytest.raises(ValueError, match="sim adapter"):
        SessionConfig(plan=PLAN, scene=SCENE, runs_dir=tmp_path, **settings)


def test_odometry_drift_needs_a_walking_robot():
    with pytest.raises(ValueError, match="needs physics locomotion"):
        SessionConfig(plan=PLAN, scene=SCENE, adapter="sim", odometry="drift")


def test_contradicting_settings_end_the_cli_with_one_line(tmp_path):
    with pytest.raises(SystemExit, match="spingi run: perception 'markers' reads camera images"):
        main(["run", str(PLAN), "--scene", str(SCENE), "--perception", "markers", "--runs-dir", str(tmp_path)])


def test_cli_realistic_is_the_simulator_with_every_realistic_sensor(tmp_path):
    with pytest.raises(SystemExit, match="needs --adapter sim"):
        main(["run", str(PLAN), "--scene", str(SCENE), "--realistic", "--adapter", "fake", "--runs-dir", str(tmp_path)])


def test_the_scene_landmarks_are_part_of_the_world():
    world = load_world(SCENE)
    assert {lm.marker_id for lm in world.landmarks} == {30, 31, 32, 33, 34, 35}
    rack = next(lm for lm in world.landmarks if lm.marker_id == 31)
    assert (rack.height_m, rack.size_m) == (1.1, 0.16) and abs(rack.pose.yaw - 3.1416) < 1e-9
