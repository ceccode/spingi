"""The public Python surface (ADR-0029), used as a program would: by name, with plain data back."""

import json
import math
import re
import shutil
from pathlib import Path

import pytest

from spingi import api
from spingi.core.localization import FACING_LIMIT_RAD
from spingi.core.plan import validate_plan
from spingi.metrics import Gate
from spingi.perception.view import HEAD_CAMERA
from spingi.scenes import load_scene
from spingi.session import MODES
from spingi.skills import default_registry
from spingi.skills.navigate import LANDMARK_HALF_FOV_RAD, TAG_READ_RANGE

GOLDEN = Path("tests/golden/episodes/inspection_round")
TWO_STEPS = {
    "id": "hello",
    "steps": [{"skill": "say", "params": {"text": "hi"}}, {"skill": "navigate", "params": {"to": "shelf_A"}}],
}


# --- what there is -----------------------------------------------------------------------------------------------
def test_robots_and_the_skills_each_one_can_run():
    assert [(r.alias, r.name, r.kind) for r in api.robots()] == [
        ("g1", "unitree_g1", "humanoid"),
        ("go2", "unitree_go2", "quadruped"),
    ]
    assert api.robots()[1].capabilities == ["camera", "locomotion"]
    everything = {s.name for s in api.skills()}
    assert everything == {"navigate", "detect", "pick", "place", "inspect", "wait_for_human", "say"}
    assert everything - {s.name for s in api.skills("go2")} == {"pick", "place"}
    navigate = next(s for s in api.skills() if s.name == "navigate")
    assert navigate.summary.startswith("navigate(to, via=[]): brings the robot to a known location")
    assert navigate.requires == ["locomotion"] and "max_speed" in navigate.parameters["properties"]
    assert navigate.parameters["additionalProperties"] is False


def test_an_unknown_robot_is_a_value_error_that_names_the_known_ones():
    with pytest.raises(ValueError, match="unknown robot 'r2d2'; known: g1, go2"):
        api.skills("r2d2")


def test_the_plan_schema_of_a_robot_has_only_its_skills():
    def names(robot):
        variants = api.plan_schema(robot)["properties"]["steps"]["items"]["anyOf"]
        return {v["properties"]["skill"]["const"] for v in variants}

    assert names("g1") - names("go2") == {"pick", "place"}
    assert api.scene_schema()["properties"].keys() >= {"locations", "obstacles", "terrain", "safety"}


def test_samples_are_listed_and_the_working_folders_own_come_first(tmp_path):
    listed = {s.name: s for s in api.scenes(tmp_path)}
    assert listed["warehouse_small"].source == "sample"
    assert listed["warehouse_small"].summary.startswith("Small warehouse: two shelving racks")
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "warehouse_small.yaml").write_text("# My own warehouse. Second sentence.\nlocations: {}\n")
    (tmp_path / "scenes" / "kitchen.yaml").write_text("locations: {}\n")
    mine = api.scenes(tmp_path)
    assert [(s.name, s.source) for s in mine[:2]] == [("kitchen", "folder"), ("warehouse_small", "folder")]
    assert mine[1].summary == "My own warehouse." and [s.name for s in mine].count("warehouse_small") == 1
    assert {p.name for p in api.plans(tmp_path)} >= {"yard_patrol", "demo_inspection_round"}


def test_a_sample_can_be_read_to_start_from_it(tmp_path):
    assert api.read_scene("lab_small", tmp_path) == Path("sim/scenes/lab_small.yaml").read_text()
    assert api.read_plan("yard_patrol", tmp_path).startswith("# Yard patrol")
    with pytest.raises(api.UnknownSample, match="no scene 'kitchen'"):
        api.read_scene("kitchen", tmp_path)


# --- scenes ------------------------------------------------------------------------------------------------------
def test_a_scene_described_as_data_and_as_the_planners_text():
    scene = api.describe_scene("warehouse_small")
    assert scene.name == "warehouse_small" and scene.start == {"x": 0.0, "y": 0.0, "yaw": 0.0}
    assert scene.locations["shelf_A"] == {"x": 4.0, "y": 3.0, "yaw": 0.0, "tolerance_m": 0.15}
    assert scene.objects["red_box_01"]["cls"] == "red_box" and scene.objects["red_box_01"]["z"] == 0.95
    assert scene.obstacles[0].keys() == {"index", "x", "y", "w", "d", "h"}
    assert scene.routes["dock->shelf_A"] == ["aisle_in"] and scene.terrain is None
    assert scene.safety["max_speed"] == 0.8 and scene.safety["geofence"]["x_max"] == 10.0
    assert "- shelf_A: 4.00, 3.00" in scene.text and "Routes" in scene.text
    assert api.describe_scene("yard_outdoor").terrain["surface"] == "asphalt"
    json.dumps(scene.model_dump())  # plain data


def test_checking_a_scene_by_name_or_as_text():
    fenced = api.check_scene("lab_geofence")
    assert fenced.valid and fenced.errors == [] and fenced.description.name == "lab_geofence"
    assert [w.code for w in fenced.warnings] == ["outside_geofence"]

    typo = api.check_scene(text="locations:\n  dock: { pose: { x: 0, y: 0 } }\nobstacle: []\n")
    assert (typo.valid, typo.errors, typo.description) == (
        False,
        ["obstacle: Extra inputs are not permitted"],
        None,
    )
    assert api.check_scene(text="locations: {dock: [}\n").errors[0].startswith("not valid YAML")
    with pytest.raises(ValueError, match="a scene or its text, one of the two"):
        api.check_scene()


def test_saving_a_scene_makes_it_one_of_the_working_folders(tmp_path):
    text = "# A room.\nlocations:\n  door: { pose: { x: 1, y: 0 } }"
    path = api.save_scene("room", text, tmp_path)
    assert path == tmp_path / "scenes" / "room.yaml" and path.read_text() == text + "\n"
    assert api.describe_scene("room", tmp_path).locations.keys() == {"door"}
    assert api.scenes(tmp_path)[0].name == "room"
    with pytest.raises(FileExistsError, match="pass overwrite"):
        api.save_scene("room", text, tmp_path)
    assert api.save_scene("room", "locations: {}\n", tmp_path, overwrite=True).read_text() == "locations: {}\n"


def test_what_is_not_a_scene_is_not_saved(tmp_path):
    with pytest.raises(api.SceneError, match="obstacle: Extra inputs are not permitted"):
        api.save_scene("room", "obstacle: []\n", tmp_path)
    with pytest.raises(ValueError, match="invalid scene name"):
        api.save_scene("../outside", "locations: {}\n", tmp_path)
    assert not (tmp_path / "scenes").exists() and not (tmp_path.parent / "outside.yaml").exists()


# --- plans -------------------------------------------------------------------------------------------------------
def test_check_plan_says_what_the_run_would_say():
    """The same call as the executor's, so the same messages (ADR-0025, ADR-0026)."""
    wrong = {
        "id": "typos",
        "steps": [
            {"skill": "navigate", "params": {"to": "pump_a", "max_sped": 0.8}},
            {"skill": "inspekt", "params": {}},
        ],
    }
    check = api.check_plan(wrong, "yard_outdoor", robot="go2")
    scene = load_scene("sim/scenes/yard_outdoor.yaml")
    assert check.errors == validate_plan(
        api.parse_plan(wrong), default_registry(), frozenset({"locomotion", "camera"}), scene.locations
    )
    assert not check.valid and check.plan_id == "typos" and check.steps == 2
    assert "step 0 (navigate): unknown location 'pump_a', did you mean 'pump_A'?" in check.errors


def test_check_plan_knows_the_robot_and_warns_about_the_scene():
    no_arm = api.check_plan("demo_material_runner", "warehouse_small", robot="go2")
    assert no_arm.errors == [
        "step 3: skill 'pick' needs arm, which this robot does not have",
        "step 5: skill 'place' needs arm, which this robot does not have",
    ]
    fine = api.check_plan("yard_patrol", "yard_outdoor", robot="go2")
    assert (fine.valid, fine.errors, fine.warnings, fine.steps) == (True, [], [], 9)
    blocked = api.check_plan("tests/golden/plans/to_workstation.yaml", "lab_blocked")
    assert blocked.valid and [w.code for w in blocked.warnings] == ["leg_crosses_obstacle"]


def test_a_plan_as_text_a_mapping_or_a_file_that_is_not_a_plan(tmp_path):
    text = "id: hello\nsteps:\n  - skill: say\n    params: { text: hi }\n"
    assert api.check_plan(api.parse_plan(text), "lab_small").valid
    misspelled = {"id": "x", "steps": [{"skill": "say", "params": {"text": "hi"}, "on_failre": {}}]}
    assert api.check_plan(misspelled, "lab_small").errors == [
        "invalid plan: steps.0.on_failre: Extra inputs are not permitted"
    ]
    with pytest.raises(api.PlanError, match="not valid YAML"):
        api.parse_plan("id: [unclosed\n")
    with pytest.raises(api.SceneError):  # the scene comes first
        (tmp_path / "bad.yaml").write_text("obstacle: []\n")
        api.check_plan(text, tmp_path / "bad.yaml")


# --- running -----------------------------------------------------------------------------------------------------
def test_a_run_leaves_an_episode_and_says_what_happened(tmp_path, capsys):
    report = api.run("demo_inspection_round", runs_dir=tmp_path / "runs")
    assert (report.status, report.ok, report.steps_completed, report.steps_total) == ("success", True, 9, 9)
    assert Path(report.episode_dir, "manifest.json").is_file() and Path(report.archive).is_file()
    assert capsys.readouterr().out == ""  # a library does not print: an MCP server's stdout is its protocol

    summary = report.summary
    assert (summary.plan_id, summary.robot, summary.adapter) == ("demo_inspection_round", "unitree_g1", "fake")
    assert [s.skill for s in summary.steps][:3] == ["say", "navigate", "inspect"]
    assert all(s.outcome == "success" and s.attempts == 1 for s in summary.steps)
    assert summary.steps[1].params == {"to": "shelf_A"}
    assert summary.timeline[-1].endswith("run end: success · 9 steps completed")
    assert summary.metrics.anomalies == 1 and summary.metrics.human_requests == 0
    assert summary.viewer == f"open {api.VIEWER_URL} and drop {report.archive} on the page"
    json.dumps(report.model_dump())


def test_a_plan_given_as_data_runs_and_its_file_is_kept_with_the_runs(tmp_path):
    report = api.run(TWO_STEPS, "lab_small", runs_dir=tmp_path / "runs", zip_episode=False)
    assert report.ok and report.archive is None
    (kept,) = (tmp_path / "runs" / "plans").glob("hello-*.yaml")
    assert api.parse_plan(kept.read_text()).id == "hello"
    assert Path(report.episode_dir, "plan.yaml").read_text() == kept.read_text()
    assert report.summary.viewer.startswith("zip the folder")
    api.run(TWO_STEPS, "lab_small", runs_dir=tmp_path / "runs")
    assert len(list((tmp_path / "runs" / "plans").glob("*.yaml"))) == 1  # the same plan is the same file


def test_a_step_that_fails_shows_its_attempts_and_the_fixed_answer(tmp_path):
    """Perception that misses everything: detect retries, then asks; the answer is `on_failure`, every time."""
    report = api.run("demo_material_runner", "warehouse_small", noise=1.0, runs_dir=tmp_path / "runs")
    assert report.status == "aborted" and not report.ok
    detect = next(s for s in report.summary.steps if s.skill == "detect")
    assert (detect.outcome, detect.attempts) == ("recoverable", 3)
    assert detect.reason == "found 0 'red_box', expected 1"
    assert report.summary.metrics.human_requests == 1 and report.summary.metrics.retries == 2

    skipped = api.run("demo_material_runner", "warehouse_small", noise=1.0, on_failure="skip", runs_dir=tmp_path / "r")
    assert next(s for s in skipped.summary.steps if s.skill == "detect").outcome == "skipped"


def test_a_plan_wrong_for_its_scene_ends_as_invalid_plan_with_its_errors(tmp_path):
    wrong = {"id": "lost", "steps": [{"skill": "navigate", "params": {"to": "nowhere"}}]}
    report = api.run(wrong, "lab_small", runs_dir=tmp_path / "runs")
    assert report.status == "invalid_plan" and report.summary.steps == []
    assert report.summary.errors == ["step 0 (navigate): unknown location 'nowhere'"]


def test_what_cannot_start_raises_before_a_run_folder_is_made(tmp_path):
    runs = tmp_path / "runs"
    with pytest.raises(api.UnknownSample):
        api.run("no_such_plan", runs_dir=runs)
    with pytest.raises(api.PlanError, match="on_failre"):
        (tmp_path / "typo.yaml").write_text(
            "id: t\nsteps:\n  - skill: say\n    params: {text: hi}\n    on_failre: {}\n"
        )
        api.run(tmp_path / "typo.yaml", runs_dir=runs)
    with pytest.raises(api.SceneError, match="obstacle"):
        (tmp_path / "scene.yaml").write_text("obstacle: []\n")
        api.run("demo_inspection_round", tmp_path / "scene.yaml", runs_dir=runs)
    with pytest.raises(ValueError, match="unknown mode 'hover'"):
        api.run("demo_inspection_round", mode="hover", runs_dir=runs)
    with pytest.raises(ValueError, match="videos are rendered by the simulator"):
        api.run("demo_inspection_round", camera_video=True, runs_dir=runs)
    assert not runs.exists()


def test_events_reach_the_caller_as_they_happen(tmp_path):
    seen = []
    api.run(TWO_STEPS, "lab_small", runs_dir=tmp_path / "runs", on_event=lambda e: seen.append(e.kind))
    assert seen[0] == "safety.speed_capped" and seen.count("step.start") == 2 and "run.end" in seen


async def test_arun_is_the_same_inside_an_event_loop(tmp_path):
    report = await api.arun(TWO_STEPS, "lab_small", runs_dir=tmp_path / "runs")
    assert report.ok and report.steps_total == 2


def test_bench_runs_a_plan_many_times_and_checks_the_gate(tmp_path):
    done = []
    report = api.bench(
        "demo_inspection_round", runs=4, mode="fake", runs_dir=tmp_path / "runs", on_run=lambda i, n, m: done.append(i)
    )
    assert (report.runs, report.success_rate, report.passed, done) == (4, 1.0, True, [1, 2, 3, 4])
    (written,) = (tmp_path / "runs").glob("bench-*/report.json")
    assert json.loads(written.read_text())["passed"] is True
    failing = api.bench(
        "demo_material_runner", "warehouse_small", runs=3, mode="fake", noise=1.0, runs_dir=tmp_path / "r"
    )
    assert not failing.passed and failing.success_rate == 0.0 and failing.failures == {"aborted": 3}
    with pytest.raises(ValueError, match="at least 1"):
        api.bench("demo_inspection_round", runs=0)


# --- what happened -----------------------------------------------------------------------------------------------
def test_an_episode_is_read_by_its_folder_its_id_or_as_the_latest(tmp_path):
    runs = tmp_path / "runs"
    first = api.run(TWO_STEPS, "lab_small", runs_dir=runs)
    second = api.run("demo_inspection_round", runs_dir=runs)
    assert api.episodes(runs) == [second.run_id, first.run_id]
    assert api.episode_summary(runs_dir=runs).plan_id == "demo_inspection_round"  # latest
    assert api.episode_summary(first.run_id, runs_dir=runs).plan_id == "hello"
    assert api.episode_summary(Path(first.episode_dir)).run_id == first.run_id
    assert api.episode_summary(GOLDEN).status == "success"
    assert api.episodes(tmp_path / "none") == []
    for missing in ("r-20260101-000000-abcdef", "../outside", "latest"):
        with pytest.raises(FileNotFoundError):
            api.episode_summary(missing, runs_dir=tmp_path / "none")


def test_events_come_in_pages_and_by_kind():
    everything = api.events(GOLDEN, limit=5)
    assert everything.total > 40 and len(everything.events) == 5 and everything.offset == 0
    assert everything.events[0].keys() >= {"ts", "run_id", "kind"}
    steps = api.events(GOLDEN, kinds=["step"])
    assert steps.total == len(steps.events) and {e["kind"].split(".")[0] for e in steps.events} == {"step"}
    assert api.events(GOLDEN, kinds=["run.end"]).events[0]["status"] == "success"
    tail = api.events(GOLDEN, offset=everything.total - 2)
    assert len(tail.events) == 2 and tail.events[-1]["kind"] == "safety.disarmed"


def test_a_frame_is_an_image_inside_the_episode_and_nothing_else(tmp_path):
    episode = shutil.copytree(GOLDEN, tmp_path / "episode")
    (episode / "frames").mkdir(exist_ok=True)
    (episode / "frames" / "sim-1.png").write_bytes(b"\x89PNG")
    (episode / "frames" / "notes.txt").write_text("not an image")
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")
    assert api.frame(episode, "frames/sim-1.png") == (episode / "frames" / "sim-1.png").resolve()
    for ref in ("frames/notes.txt", "../secret.png", "frames/../../secret.png", "manifest.json", "frames/none.png"):
        with pytest.raises(ValueError, match="no frame"):
            api.frame(episode, ref)
    (episode / "frames" / "link.png").symlink_to(tmp_path / "secret.png")
    with pytest.raises(ValueError, match="no frame"):
        api.frame(episode, "frames/link.png")


# --- guides ------------------------------------------------------------------------------------------------------
def yaml_blocks(topic: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", api.guide(topic), re.S)


def test_the_guides_are_four_and_an_unknown_topic_names_them():
    assert api.guides() == ["landmarks", "plan", "scene", "workflow"]
    assert api.guide("workflow").startswith("# Working with Spingi")
    with pytest.raises(ValueError, match="no guide 'cooking'; known: landmarks, plan, scene, workflow"):
        api.guide("cooking")


def test_the_scene_a_guide_teaches_with_is_a_scene_without_warnings_and_its_plan_runs_in_it(tmp_path):
    """A guide that teaches a format with an example that does not pass would teach the mistake."""
    scene, ground = yaml_blocks("scene")
    check = api.check_scene(text=scene)
    assert check.valid and check.warnings == [], (check.errors, check.warnings)
    assert api.check_scene(text=ground + "safety: {}\n").valid  # the outdoor fragment, alone

    api.save_scene("stock_room", scene, tmp_path)
    (plan,) = yaml_blocks("plan")
    checked = api.check_plan(api.parse_plan(plan), "stock_room", root=tmp_path)
    assert checked.valid and checked.warnings == [], (checked.errors, checked.warnings)
    assert api.run(api.parse_plan(plan), "stock_room", root=tmp_path).ok

    (tags,) = yaml_blocks("landmarks")
    assert api.check_scene(text=tags).valid


def test_what_the_guides_say_about_the_robot_is_what_the_code_does():
    scene, plan, landmarks, workflow = (api.guide(t) for t in ("scene", "plan", "landmarks", "workflow"))
    assert f"up to {HEAD_CAMERA.max_range_m:g} m away, within 40 degrees" in scene
    assert math.degrees(HEAD_CAMERA.half_fov_rad) == pytest.approx(40)
    assert f"read up to {TAG_READ_RANGE:g} times its side away" in landmarks
    assert math.degrees(LANDMARK_HALF_FOV_RAD) == pytest.approx(45) and "within 45 degrees" in landmarks
    assert math.degrees(FACING_LIMIT_RAD) == pytest.approx(75) and "within 75 degrees" in landmarks
    gate = Gate()
    assert (gate.min_success_rate, gate.max_needs_human_per_100, gate.max_fatal_runs) == (0.95, 5.0, 0)
    assert "at least 95 % success, at most 5 operator requests per 100 runs" in workflow
    for skill in api.skills():
        assert f"`{skill.name}`" in plan, skill.name
    for mode in MODES:
        assert f"`{mode}`" in workflow


# --- references held to the working folder -----------------------------------------------------------------------
def test_a_confined_reference_is_a_file_of_the_working_folder_or_a_sample(tmp_path):
    work = tmp_path / "work"
    (work / "scenes").mkdir(parents=True)
    (work / "scenes" / "room.yaml").write_text("locations: {}\n")
    outside = tmp_path / "outside.yaml"
    outside.write_text("locations: {}\n")
    (work / "link.yaml").symlink_to(outside)

    assert api.locate_scene("room", work, confined=True) == work / "scenes" / "room.yaml"
    assert api.locate_scene("lab_small", work, confined=True).name == "lab_small.yaml"  # a sample
    assert api.locate_plan("yard_patrol", work, confined=True).name == "yard_patrol.yaml"
    for ref in (outside, "../outside.yaml", "link.yaml"):
        with pytest.raises(api.OutsideWorkspace, match="is outside the working folder"):
            api.locate_scene(ref, work, confined=True)
    assert api.locate_scene(outside, work) == outside  # a program that names its own files is not confined
