"""The command line as a person uses it: which session each command asks for, what it prints, its exit code."""

import json
import shutil
from pathlib import Path

import pytest

import spingi
from spingi import api, cli
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


def test_json_prints_the_report_of_the_api_and_nothing_else(tmp_path, capsys):
    assert main(run("--runs-dir", str(tmp_path), "--zip", "--json")) == 0
    out = capsys.readouterr()
    report = api.RunReport.model_validate_json(out.out)  # the whole of standard output is one report
    assert (report.status, report.ok, report.steps_completed, report.steps_total) == ("success", True, 8, 8)
    assert Path(report.episode_dir).parent == tmp_path and Path(report.archive).is_file()
    assert report == api.RunReport.of(_ended(report)) and out.err == ""
    assert report.summary.timeline[-1].endswith("run end: success · 8 steps completed")

    assert main(run("--runs-dir", str(tmp_path), "--noise", "1.0", "--json")) == 1
    failed = json.loads(capsys.readouterr().out)
    assert (failed["status"], failed["ok"]) == ("aborted", False) and failed["summary"]["steps"][-1]["reason"]


def _ended(report: api.RunReport) -> SessionResult:
    """The session a report came from, as far as the report says."""
    return SessionResult(
        run_id=report.run_id, run_dir=Path(report.episode_dir), status=report.status,
        steps_completed=report.steps_completed, steps_total=report.steps_total, log=EventLog(run_id=report.run_id),
        archive=Path(report.archive), sim_time_s=report.sim_time_s,
    )  # fmt: skip


def test_json_says_why_a_file_is_refused_before_the_run(tmp_path, capsys):
    plan = tmp_path / "typo.yaml"
    plan.write_text("id: typo\nsteps:\n  - skill: say\n    params: { text: hi }\n    on_failre: { then: abort }\n")
    assert main(["run", str(plan), "--runs-dir", str(tmp_path / "runs"), "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "status": "invalid_plan",
        "errors": [f"invalid plan ({plan}): steps.0.on_failre: Extra inputs are not permitted"],
    }
    scene = tmp_path / "scene.yaml"
    scene.write_text("locations: {}\nobstacle: []\n")
    assert main(["run", str(PLAN), "--scene", str(scene), "--runs-dir", str(tmp_path / "runs"), "--json"]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert (refused["ok"], refused["status"]) == (False, "invalid_scene") and "obstacle" in refused["errors"][0]
    assert not (tmp_path / "runs").exists()


def test_json_is_not_for_a_person_at_the_console(tmp_path):
    with pytest.raises(SystemExit, match="--json prints one JSON object and nothing else: not with --operator console"):
        main(run("--runs-dir", str(tmp_path), "--operator", "console", "--json"))
    assert list(tmp_path.iterdir()) == []


def test_a_misspelled_parameter_ends_the_run_as_an_invalid_plan(tmp_path, capsys):
    plan = tmp_path / "typo.yaml"
    plan.write_text("id: typo\nsteps:\n  - skill: navigate\n    params: { to: shelf_A, max_sped: 0.8 }\n")
    assert main(["run", str(plan), "--runs-dir", str(tmp_path / "runs")]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("INVALID_PLAN · 0/1 steps · ")
    (invalid,) = [json.loads(line) for line in out if line.startswith("{") and '"plan.invalid"' in line]
    assert invalid["errors"] == ["step 0 (navigate): invalid parameters: max_sped: Extra inputs are not permitted"]


def test_a_plan_file_that_is_not_a_task_plan_is_refused_before_any_robot_starts(sessions, tmp_path, capsys):
    plan = tmp_path / "typo.yaml"
    plan.write_text("id: typo\nsteps:\n  - skill: say\n    params: { text: hi }\n    on_failre: { then: abort }\n")
    runs = tmp_path / "runs"
    for command in (["run"], ["bench", "--runs", "2"]):
        assert main([*command, str(plan), "--runs-dir", str(runs)]) == 1
        last = capsys.readouterr().out.strip().splitlines()[-1]
        assert last == f"INVALID_PLAN · invalid plan ({plan}): steps.0.on_failre: Extra inputs are not permitted"
    assert sessions == [] and not runs.exists()


def test_the_automatic_operator_prints_every_event(tmp_path, capsys):
    main(run("--runs-dir", str(tmp_path)))
    kinds = [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert kinds[0] == "safety.speed_capped" and "run.start" in kinds and "run.end" in kinds
    assert kinds[-1] == "safety.disarmed"


def test_a_sample_runs_by_its_name_on_the_default_scene(sessions, tmp_path):
    assert main(["run", "demo_inspection_round", "--runs-dir", str(tmp_path), "--quiet"]) == 0
    cfg, _ = sessions[0]
    assert cfg.plan == Path("plans/demo_inspection_round.yaml") and cfg.scene == Path("sim/scenes/lab_small.yaml")


def test_a_plan_that_is_neither_a_file_nor_a_sample_ends_the_command_in_one_line(sessions, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["run", "plans/no_such_plan.yaml"])
    assert exit_.value.code == 2 and sessions == []
    assert "no plan 'plans/no_such_plan.yaml': not a file" in capsys.readouterr().err


def test_version_prints_the_packages_version_and_needs_no_command(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out == f"spingi {spingi.__version__}\n"


def test_scenes_and_plans_list_the_samples_and_print_one(capsys):
    assert main(["scenes"]) == 0
    listed = dict(line.split(maxsplit=1) for line in capsys.readouterr().out.splitlines())
    assert listed["lab_small"].startswith("Lab scene: four known waypoints")
    assert {"warehouse_small", "yard_outdoor"} <= set(listed)
    assert main(["plans", "--show", "yard_patrol"]) == 0
    assert capsys.readouterr().out == Path("plans/yard_patrol.yaml").read_text()
    assert main(["plans", "--show", "nothing"]) == 1
    assert "no plan 'nothing': not a file, not in plans/, and not one of the samples (demo_" in (
        capsys.readouterr().err
    )


def test_a_scene_file_that_is_not_a_scene_is_refused_before_any_robot_starts(sessions, tmp_path, capsys):
    scene = tmp_path / "typo.yaml"
    scene.write_text("locations:\n  dock: { pose: { x: 0, y: 0 } }\nobstacle:\n  - { x: 1, y: 0 }\n")
    runs = tmp_path / "runs"
    for command in (["run"], ["bench", "--runs", "2"]):
        assert main([*command, str(PLAN), "--scene", str(scene), "--runs-dir", str(runs)]) == 1
        last = capsys.readouterr().out.strip().splitlines()[-1]
        assert last == f"INVALID_SCENE · invalid scene ({scene}): obstacle: Extra inputs are not permitted"
    assert sessions == [] and not runs.exists()


def test_check_says_what_a_scene_holds_and_what_a_robot_would_meet_in_it(capsys):
    assert main(["check", "yard_outdoor"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "scene sim/scenes/yard_outdoor.yaml: 9 locations, 7 obstacles, 3 objects, 16 landmarks, terrain"
    assert out[-1] == "OK · 0 errors, 0 warnings"

    assert main(["check", "lab_geofence"]) == 0  # a warning is not an error
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "warning: location 'workstation_B' is outside the geofence: reaching it is an emergency stop"
    assert out[-1] == "OK · 0 errors, 1 warning"
    assert main(["check", "lab_geofence", "--strict"]) == 1


def test_check_takes_a_plan_and_its_robot(capsys):
    blocked = ["check", "lab_blocked", "--plan", "tests/golden/plans/to_workstation.yaml"]
    assert main(blocked) == 0
    assert "warning: step 0 (navigate): the leg from the start to workstation_B crosses obstacle 0" in (
        capsys.readouterr().out
    )
    assert main(["check", "warehouse_small", "--plan", "demo_material_runner", "--robot", "go2"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "error: plan: step 3: skill 'pick' needs arm, which this robot does not have"
    assert out[-1] == "INVALID · 2 errors, 0 warnings"


def test_check_lists_every_error_of_a_file_that_is_not_a_scene(tmp_path, capsys):
    scene = tmp_path / "typo.yaml"
    scene.write_text("locations:\n  dock: { pose: { x: 0, y: 0, yaww: 1 } }\nobstacle: []\n")
    assert main(["check", str(scene)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "error: scene: locations.dock.pose.yaww: Extra inputs are not permitted",
        "error: scene: obstacle: Extra inputs are not permitted",
        "",
        "INVALID · 2 errors, 0 warnings",
    ]


def test_map_draws_the_scene_with_what_the_checks_found(tmp_path, capsys, monkeypatch):
    out = tmp_path / "blocked.png"
    plan = "tests/golden/plans/to_workstation.yaml"
    assert main(["map", "lab_blocked", "--plan", plan, "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == f"map: {out} · 1 warning marked"
    assert out.read_bytes().startswith(b"\x89PNG")

    scene = Path("sim/scenes/lab_small.yaml").resolve()
    monkeypatch.chdir(tmp_path)  # without --out the picture goes into the current folder, named after the scene
    assert main(["map", str(scene)]) == 0
    assert (tmp_path / "lab_small-map.png").is_file()

    (tmp_path / "typo.yaml").write_text("obstacle: []\n")
    assert main(["map", "typo.yaml"]) == 1
    assert "error: scene: obstacle: Extra inputs are not permitted" in capsys.readouterr().err


def test_skills_and_robots_list_what_each_robot_can_do(capsys):
    assert main(["skills", "--robot", "go2"]) == 0
    names = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
    assert "navigate" in names and "pick" not in names and "place" not in names
    assert main(["robots"]) == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["g1", "go2"]


def test_replay_says_whether_the_episode_behaves_the_same(capsys):
    assert main(["replay", "tests/golden/episodes/inspection_round"]) == 0
    assert capsys.readouterr().out.startswith("same ")


def test_replay_reports_an_episode_whose_plan_is_no_longer_valid_and_goes_on(tmp_path, capsys):
    old = shutil.copytree("tests/golden/episodes/inspection_round", tmp_path / "old")
    plan = old / "plan.yaml"
    plan.write_text(plan.read_text(encoding="utf-8").replace("on_failure:", "on_failre:", 1), encoding="utf-8")
    assert main(["replay", str(old), "tests/golden/episodes/inspection_round"]) == 1
    first, second = capsys.readouterr().out.splitlines()[:2]
    assert first.startswith(f"INVALID   {old}  invalid plan (") and "on_failre: Extra inputs are not permitted" in first
    assert second.startswith("same ")


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
