"""A scene file has one schema, and every reader goes through it (ADR-0028)."""

import copy
import json
import typing
from pathlib import Path

import pytest
import yaml

from spingi import scenes
from spingi.adapters.sim_mujoco import terrain as sim_terrain
from spingi.adapters.sim_mujoco.scene import load_scene_yaml
from spingi.adapters.sim_mujoco.values import SceneError as SimSceneError
from spingi.safety.limits import Geofence, SafetyLimits
from spingi.scenes import (
    SceneError,
    SceneFile,
    json_schema,
    load_routes,
    load_safety_limits,
    load_scene,
    load_world,
    parse_scene,
    scene_errors,
)

RUNTIME = Path(__file__).resolve().parents[2]
SCHEMA = RUNTIME.parent / "docs" / "schemas" / "scene.schema.json"
EVERY_SCENE = sorted([*RUNTIME.glob("sim/scenes/*.yaml"), *RUNTIME.glob("tests/golden/episodes/*/scene.yaml")])

GOOD = {
    "robot": {"pose": {"x": 0, "y": 0, "yaw": 0}},
    "locations": {
        "dock": {"pose": {"x": 0, "y": 0}},
        "shelf": {"pose": {"x": 4, "y": 0, "yaw": 0}, "tolerance_m": 0.2},
    },
    "obstacles": [{"x": 5.2, "y": 0, "w": 0.4, "d": 2, "h": 1.6, "color": "#808080"}],
    "objects": {"box_1": {"cls": "box", "marker_id": 7, "pose": {"x": 4.9, "y": 0, "z": 1.0}}},
    "landmarks": [{"marker_id": 20, "x": 6, "y": 1, "facing": 3.14, "size_m": 0.3}],
    "routes": {"dock->shelf": []},
    "safety": {"geofence": {"x_min": -1, "x_max": 8, "y_min": -3, "y_max": 3}, "max_speed": 0.8},
}


def scene(**sections) -> dict:
    """The good scene with some sections replaced."""
    return {**copy.deepcopy(GOOD), **sections}


def changed(path: str, value) -> dict:
    """The good scene with one value set, `locations.dock.pose.yaww` style; a list index is a number."""
    raw = copy.deepcopy(GOOD)
    node = raw
    *parents, last = path.split(".")
    for key in parents:
        node = node[int(key)] if key.isdigit() else node[key]
    node[last] = value
    return raw


@pytest.mark.parametrize("path", EVERY_SCENE, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_scene_of_the_repository_is_a_scene(path):
    assert isinstance(load_scene(path), SceneFile)
    assert scene_errors(yaml.safe_load(path.read_text())) == []


def test_the_good_scene_is_good():
    assert scene_errors(GOOD) == []


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("obstacle", "obstacle: Extra inputs are not permitted"),  # the section that leaves the wall out
        ("locations.dock.pose.yaww", "locations.dock.pose.yaww: Extra inputs are not permitted"),
        ("locations.dock.tolerence_m", "locations.dock.tolerence_m: Extra inputs are not permitted"),
        ("obstacles.0.height", "obstacles.0.height: Extra inputs are not permitted"),
        ("objects.box_1.colour", "objects.box_1.colour: Extra inputs are not permitted"),
        ("landmarks.0.facng", "landmarks.0.facng: Extra inputs are not permitted"),
        ("safety.max_sped", "safety.max_sped: Extra inputs are not permitted"),  # a speed limit that is not there
        ("safety.geofence.xmin", "safety.geofence.xmin: Extra inputs are not permitted"),
        ("robot.batery_pct", "robot.batery_pct: Extra inputs are not permitted"),
    ],
)
def test_a_key_nobody_declares_is_an_error_wherever_it_is(path, line):
    assert scene_errors(changed(path, 1)) == [line]


@pytest.mark.parametrize(
    ("path", "value", "says"),
    [
        ("locations.dock.pose.x", float("nan"), "finite number"),
        ("locations.dock.pose.x", float("inf"), "finite number"),
        ("locations.dock.pose.x", True, "valid number"),
        ("locations.dock.pose.x", "0", "valid number"),
        ("locations.dock.tolerance_m", 0, "greater than 0"),
        ("obstacles.0.w", -1, "greater than 0"),
        ("obstacles.0.color", "grey", "should match pattern"),
        ("objects.box_1.cls", "red box", "should match pattern"),
        ("objects.box_1.marker_id", 64, "less than or equal to 63"),
        ("objects.box_1.marker_id", 7.0, "valid integer"),
        ("objects.box_1.size", [0.1, 0.1], "at least 3 items"),
        ("objects.box_1.size", [0.1, 0.1, 0], "greater than 0"),
        ("objects.box_1.confidence", 2, "less than or equal to 1"),
        ("landmarks.0.size_m", 0.9, "less than or equal to 0.6"),
        ("landmarks.0.height_m", 5, "less than or equal to 3"),
        ("landmarks.0.post", "no", "valid boolean"),
        ("robot.battery_pct", 150, "less than or equal to 100"),
        ("safety.min_battery_pct", -1, "greater than or equal to 0"),
        ("safety.max_speed", 0, "greater than 0"),
    ],
)
def test_a_value_that_cannot_be_is_refused_with_its_place(path, value, says):
    (error,) = scene_errors(changed(path, value))
    assert error.startswith(f"{path}") and says in error


def test_an_object_position_needs_its_height():
    raw = changed("objects.box_1.pose", {"x": 1, "y": 2})
    assert scene_errors(raw) == ["objects.box_1.pose.z: Field required"]


@pytest.mark.parametrize("name", ['x" /><include file="/etc/passwd', "a b", "<b>", "", "n" * 65])
def test_names_are_one_conservative_alphabet(name):
    for section in ("locations", "objects"):
        entry = {"pose": {"x": 0, "y": 0}} if section == "locations" else {"cls": "box"}
        (error,) = scene_errors(scene(**{section: {name: entry}, "routes": {}}))
        assert error.startswith(f"{section}.{name}: String should match pattern")


def test_a_fence_or_an_extent_with_its_sides_swapped_contains_nothing():
    fence = {"x_min": 8, "x_max": -1, "y_min": -3, "y_max": 3}
    (error,) = scene_errors(changed("safety.geofence", fence))
    assert error.startswith("safety.geofence: x_min must be less than x_max")
    with pytest.raises(ValueError, match="contains nothing"):
        Geofence(x_min=1, x_max=1, y_min=0, y_max=1)


def test_a_misspelled_limit_is_refused_by_the_limits_themselves():
    with pytest.raises(ValueError, match="max_sped"):
        SafetyLimits.model_validate({"max_sped": 0.5})


# --- the scene as a whole ----------------------------------------------------------------------------------------
def test_a_marker_id_names_one_tag_objects_and_landmarks_together():
    clash = scene(landmarks=[{"marker_id": 7, "x": 6, "y": 1}])
    assert scene_errors(clash) == ["marker_id 7 is used by both box_1 and landmark 0"]
    twice = scene(landmarks=[{"marker_id": 20, "x": 6, "y": 1}, {"marker_id": 20, "x": 2, "y": 1}])
    assert scene_errors(twice) == ["marker_id 20 is used by both landmark 0 and landmark 1"]


def test_a_route_names_locations_of_its_scene():
    assert scene_errors(scene(routes={"dock->shelf": ["aisle"]})) == [
        "routes: 'dock->shelf' names 'aisle', not a location here"
    ]
    assert scene_errors(scene(routes={"dock to shelf": []})) == [
        "routes: invalid route 'dock to shelf': expected 'from->to' with location names"
    ]


def test_the_ground_must_reach_as_far_as_the_fence():
    ground = {"extent": {"x_min": 0, "x_max": 8, "y_min": -3, "y_max": 3}, "surface": "asphalt"}
    assert scene_errors(scene(terrain=ground)) == [
        "terrain.extent must cover the safety geofence: the robot would walk off the ground"
    ]


def test_every_problem_is_reported_at_once():
    raw = changed("obstacle", [])
    raw["locations"]["dock"]["pose"]["yaww"] = 1
    raw["routes"] = {"dock->nowhere": []}
    with pytest.raises(SceneError) as error:
        parse_scene(raw, "mine.yaml")
    assert error.value.errors == [
        "locations.dock.pose.yaww: Extra inputs are not permitted",
        "obstacle: Extra inputs are not permitted",
    ]  # the checks of the whole scene come once its parts are right
    assert str(error.value).startswith("invalid scene (mine.yaml): locations.dock.pose.yaww: Extra inputs")


# --- the ground --------------------------------------------------------------------------------------------------
def ground(**over) -> dict:
    base = {"extent": {"x_min": -2, "x_max": 9, "y_min": -4, "y_max": 4}, "cell_m": 0.1, "surface": "asphalt"}
    return scene(terrain={**base, **over})


def test_a_ground_with_regions_ramps_and_raised_areas_is_a_scene():
    raw = ground(
        regions=[{"surface": "gravel", "x": [1, 3], "y": [-1, 1], "roughness_m": 0.02}],
        ramps=[{"x": [5, 7], "y": [-1, 1], "to_m": 0.4, "along": "x"}],
        raised=[{"x": [7, 9], "y": [-1, 1], "height_m": 0.4}],
    )
    assert scene_errors(raw) == []


@pytest.mark.parametrize(
    ("over", "says"),
    [
        ({"surface": "ice"}, "terrain.surface: Input should be 'floor', 'concrete'"),
        ({"cell_m": 0.01}, "terrain.cell_m: Input should be greater than or equal to 0.08"),
        ({"cell_m": 0.5}, "terrain.cell_m: Input should be less than or equal to 0.25"),
        ({"seed": -1}, "terrain.seed: Input should be greater than or equal to 0"),
        ({"extent": {"x_min": 9, "x_max": -2, "y_min": -4, "y_max": 4}}, "terrain.extent: x_min must be less"),
        ({"regions": [{"surface": "gravel", "x": [3, 1], "y": [0, 1]}]}, "terrain.regions.0: x must go from"),
        ({"regions": [{"surfce": "gravel", "x": [1, 3], "y": [0, 1]}]}, "terrain.regions.0.surface: Field required"),
        ({"regions": [{"surface": "dirt", "x": [1, 3], "y": [0, 1], "roughness_m": 0.5}]}, "roughness_m: Input"),
        ({"ramps": [{"x": [1, 2], "y": [0, 1], "to_m": 2.0}]}, "terrain.ramps.0: steeper than 45 degrees"),
        ({"ramps": [{"x": [1, 2], "y": [0, 1], "to_m": 0.2, "along": "up"}]}, "terrain.ramps.0.along: Input"),
        ({"raised": [{"x": [1, 2], "y": [0, 1], "height_m": -0.5}]}, "terrain.raised.0.height_m: Input"),  # a pit
        ({"raised": [{"x": [1, 2], "y": [0, 1], "height_m": 4}]}, "less than or equal to 3"),
    ],
)
def test_a_ground_that_cannot_be_built_is_refused(over, says):
    errors = scene_errors(ground(**over))
    assert errors and any(says in e for e in errors), errors


def test_a_ground_too_large_for_its_grid_is_a_mistake_in_the_scene():
    huge = {"x_min": -500, "x_max": 500, "y_min": -500, "y_max": 500}
    raw = ground(extent=huge, cell_m=0.08)
    raw["safety"] = {}
    (error,) = scene_errors(raw)
    assert error.startswith("terrain: grid larger than 2000000 samples")


def test_the_simulator_and_the_schema_share_their_limits():
    assert tuple(sim_terrain.SURFACES) == scenes.SURFACE_NAMES == typing.get_args(scenes.Surface)
    assert sim_terrain.MIN_CELL_M is scenes.MIN_CELL_M and sim_terrain.MAX_SAMPLES is scenes.MAX_SAMPLES
    assert SimSceneError is SceneError  # one error type for a scene, whoever finds the problem


# --- reading -----------------------------------------------------------------------------------------------------
def write(tmp_path, raw) -> Path:
    path = tmp_path / "scene.yaml"
    path.write_text(raw if isinstance(raw, str) else yaml.safe_dump(raw))
    return path


def test_an_empty_section_and_an_empty_file_take_their_defaults(tmp_path):
    empty_sections = "locations:\nobstacles:\nobjects:\nlandmarks:\nroutes:\nsafety:\nterrain:\n"
    world = load_world(write(tmp_path, empty_sections))
    assert world.locations == {} and world.robot.pose.x == 0 and world.robot.battery_pct == 100
    assert load_world(write(tmp_path, "")).objects == {}
    assert load_safety_limits(write(tmp_path, "")).geofence is None


def test_the_world_a_scene_gives(tmp_path):
    path = write(tmp_path, GOOD)
    world = load_world(path)
    assert world.locations["dock"].tolerance_m == 0.15 and world.locations["shelf"].tolerance_m == 0.2
    box = world.objects["box_1"]
    assert (box.cls, box.marker_id, box.confidence, box.pose.z) == ("box", 7, 1.0, 1.0)
    (tag,) = world.landmarks
    assert (tag.marker_id, tag.pose.yaw, tag.size_m, tag.height_m) == (20, 3.14, 0.3, 0.6)
    assert load_safety_limits(path).max_speed == 0.8 and load_safety_limits(path).estop_on_geofence
    assert load_routes(path) == {"dock->shelf": []}


def test_a_file_that_is_not_a_scene_says_so_in_one_line(tmp_path):
    with pytest.raises(SceneError, match=r"invalid scene \(.*scene\.yaml\): obstacle: Extra inputs"):
        load_world(write(tmp_path, changed("obstacle", [])))
    with pytest.raises(SceneError, match="a scene is a mapping with sections"):
        load_scene(write(tmp_path, "- just\n- a list\n"))
    with pytest.raises(SceneError, match="not valid YAML") as error:
        load_scene(write(tmp_path, "locations: {dock: [}\n"))
    assert "\n" not in str(error.value)


def test_the_simulator_reads_a_scene_file_through_the_same_schema(tmp_path):
    assert load_scene_yaml(write(tmp_path, GOOD))["locations"]["shelf"]["tolerance_m"] == 0.2
    with pytest.raises(SceneError, match="obstacle: Extra inputs are not permitted"):
        load_scene_yaml(write(tmp_path, changed("obstacle", [])))


def test_the_published_schema_matches_the_model():
    """docs/schemas/scene.schema.json is generated from the model: regenerate it with `make schemas`."""
    assert SCHEMA.exists(), f"{SCHEMA} missing: run `make schemas`"
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == json_schema(), "out of date: run `make schemas`"
    assert json_schema()["additionalProperties"] is False
