"""The command line as a person uses it: which session each command asks for, what it prints, its exit code."""

import json
from pathlib import Path

import pytest

from spingi import cli
from spingi.cli import main
from spingi.core.events import EventLog
from spingi.core.human import ScriptedHuman
from spingi.session import SessionConfig, SessionResult
from tests.unit.test_llm_planner import FakeClient, golden_as_answer, planner

PLAN = Path("plans/demo_material_runner.yaml")
SCENE = Path("sim/scenes/warehouse_small.yaml")


@pytest.fixture
def sessions(monkeypatch):
    """Records the sessions the CLI asks for instead of running them; each one succeeds."""
    asked: list[tuple[SessionConfig, object]] = []

    async def run_session(cfg, human, log=None):
        asked.append((cfg, human))
        log = log or EventLog(run_id="r-test")
        return SessionResult(
            run_id=log.run_id, run_dir=cfg.runs_dir / log.run_id, status="success", steps_completed=1, steps_total=1,
            log=log,
        )  # fmt: skip

    monkeypatch.setattr(cli, "run_session", run_session)
    return asked


def run(*args: str) -> list[str]:
    return ["run", str(PLAN), "--scene", str(SCENE), *args]


def test_run_asks_for_the_fake_adapter_with_exact_sensors_by_default(sessions, tmp_path):
    assert main(run("--runs-dir", str(tmp_path), "--quiet")) == 0
    cfg, human = sessions[0]
    assert (cfg.plan, cfg.scene, cfg.adapter, cfg.robot) == (PLAN, SCENE, "fake", "g1")
    assert (cfg.perception, cfg.locomotion, cfg.odometry, cfg.camera_noise) == ("truth", "kinematic", "truth", False)
    assert (cfg.view, cfg.realtime, cfg.record_video, cfg.record_camera, cfg.zip_episode) == (False,) * 5
    assert cfg.write_episode and cfg.runs_dir == tmp_path
    assert isinstance(human, ScriptedHuman)


def test_realistic_turns_on_every_simulated_sensor(sessions, tmp_path):
    main(run("--realistic", "--runs-dir", str(tmp_path), "--quiet"))
    cfg, _ = sessions[0]
    assert (cfg.adapter, cfg.perception, cfg.locomotion, cfg.odometry, cfg.camera_noise) == (
        "sim", "markers", "physics", "drift", True,
    )  # fmt: skip


def test_run_flags_reach_the_session(sessions, tmp_path):
    flags = ["--adapter", "sim", "--robot", "go2", "--physics", "--drift", "--camera-noise", "--view", "--record"]
    flags += ["--camera-video", "--realtime", "--zip", "--noise", "0.2", "--sigma", "0.05", "--seed", "7"]
    main(run(*flags, "--runs-dir", str(tmp_path), "--quiet"))
    cfg, _ = sessions[0]
    assert (cfg.adapter, cfg.robot, cfg.locomotion, cfg.odometry, cfg.camera_noise) == (
        "sim", "go2", "physics", "drift", True,
    )  # fmt: skip
    assert (cfg.view, cfg.record_video, cfg.record_camera, cfg.realtime, cfg.zip_episode) == (True,) * 5
    assert (cfg.perception_noise, cfg.position_sigma_m, cfg.seed) == (0.2, 0.05, 7)


def test_a_run_prints_its_summary_and_exits_with_its_status(tmp_path, capsys):
    assert main(run("--runs-dir", str(tmp_path), "--quiet", "--zip")) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("SUCCESS · 8/8 steps · ") and " s simulated · episode: " in last and " · zip: " in last
    (episode,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (episode / "manifest.json").exists() and episode.with_suffix(".zip").exists()

    assert main(run("--runs-dir", str(tmp_path), "--quiet", "--noise", "1.0")) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("ABORTED · ")


def test_the_automatic_operator_prints_every_event(tmp_path, capsys):
    main(run("--runs-dir", str(tmp_path)))
    kinds = [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert kinds[0] == "safety.speed_capped" and "run.start" in kinds and "run.end" in kinds
    assert kinds[-1] == "safety.disarmed"


def test_skills_and_robots_list_what_each_robot_can_do(capsys):
    assert main(["skills", "--robot", "go2"]) == 0
    names = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
    assert "navigate" in names and "pick" not in names and "place" not in names
    assert main(["robots"]) == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["g1", "go2"]


def test_replay_says_whether_the_episode_behaves_the_same(capsys):
    assert main(["replay", "tests/golden/episodes/inspection_round"]) == 0
    assert capsys.readouterr().out.startswith("same ")


@pytest.fixture
def fake_planner(monkeypatch):
    answer = golden_as_answer("go_to_workstation")  # read now: a test may change the working directory
    monkeypatch.setattr(cli, "_llm_planner", lambda model, robot="g1": planner(FakeClient(answer)))
    monkeypatch.setattr(cli, "_api_call", lambda fn: (fn(), None))


def test_plan_run_runs_the_written_plan_once_confirmed(sessions, fake_planner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scene = Path(__file__).parents[2] / SCENE
    args = ["plan", "Go to workstation B", "--scene", str(scene), "--run", "--yes", "--robot", "go2"]
    assert main([*args, "--operator", "auto"]) == 0
    cfg, human = sessions[0]
    assert cfg.plan == Path("runs/plan-go_to_workstation.yaml") and cfg.plan.exists()
    assert (cfg.scene, cfg.adapter, cfg.robot, cfg.runs_dir) == (scene, "fake", "go2", Path("runs"))
    assert isinstance(human, ScriptedHuman)


def test_plan_run_without_a_terminal_and_without_yes_runs_nothing(sessions, fake_planner, tmp_path, capsys):
    out = tmp_path / "plan.yaml"
    assert main(["plan", "Go to workstation B", "--scene", str(SCENE), "--out", str(out), "--run"]) == 0
    assert sessions == [] and out.exists()
    assert "not run" in capsys.readouterr().out
