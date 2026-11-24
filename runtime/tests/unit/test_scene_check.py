"""Geometry checks on a scene and on a plan in it: one scene built for each warning (ADR-0028)."""

import math

import pytest

from spingi.assets import bundled_plans, bundled_scenes
from spingi.core.plan import TaskPlan, load_plan
from spingi.scene_check import CLEARANCE_M, check_plan, check_scene, segment_hits_box
from spingi.scenes import SceneFile, load_scene, parse_scene


def scene(**sections) -> SceneFile:
    base = {
        "locations": {
            "dock": {"pose": {"x": 0, "y": 0, "yaw": 0}},
            "shelf": {"pose": {"x": 4, "y": 0, "yaw": 0}},
        },
        "safety": {"geofence": {"x_min": -2, "x_max": 10, "y_min": -5, "y_max": 5}},
    }
    return parse_scene({**base, **sections})


def plan(*steps: dict) -> TaskPlan:
    return TaskPlan.model_validate({"id": "p", "steps": list(steps)})


def codes(warnings) -> list[str]:
    return [w.code for w in warnings]


def test_a_scene_with_room_around_everything_has_no_warnings():
    assert check_scene(scene()) == []


# --- the fence ---------------------------------------------------------------------------------------------------
def test_a_location_outside_the_fence_is_an_emergency_stop_waiting():
    outside = scene(locations={"dock": {"pose": {"x": 0, "y": 0}}, "yard": {"pose": {"x": 12, "y": 0}}})
    (warning,) = check_scene(outside)
    assert warning.code == "outside_geofence" and warning.where == [(12.0, 0.0)]
    assert warning.message == "location 'yard' is outside the geofence: reaching it is an emergency stop"


def test_a_plain_stop_fence_says_stop():
    fence = {"geofence": {"x_min": -2, "x_max": 10, "y_min": -5, "y_max": 5}, "estop_on_geofence": False}
    outside = scene(locations={"yard": {"pose": {"x": 12, "y": 0}}}, safety=fence)
    assert check_scene(outside)[0].message.endswith("reaching it is a stop")


def test_a_place_at_the_edge_of_the_fence_is_one_overshoot_from_leaving_it():
    edge = scene(robot={"pose": {"x": -1.9, "y": 0}})
    (warning,) = check_scene(edge)
    assert warning.code == "near_geofence"
    assert warning.message.startswith("the robot's start is 0.10 m from the edge of the geofence")


def test_without_a_fence_nothing_is_outside():
    assert check_scene(scene(locations={"far": {"pose": {"x": 500, "y": 0}}}, safety={})) == []


# --- obstacles ---------------------------------------------------------------------------------------------------
def test_a_location_inside_a_box_or_touching_it():
    inside = scene(obstacles=[{"x": 4, "y": 0, "w": 1, "d": 1}])
    assert [w.message for w in check_scene(inside)] == ["location 'shelf' is inside obstacle 0 (a 1 x 1 m box at 4, 0)"]
    touching = scene(obstacles=[{"x": 4.6, "y": 0, "w": 1, "d": 1}])
    (warning,) = check_scene(touching)
    assert warning.code == "near_obstacle" and "is 0.10 m from obstacle 0" in warning.message


def test_a_stop_in_front_of_a_shelf_is_not_a_problem():
    """Stops stand 0.3 m from tables and racks on purpose: only a robot's body inside the box is a mistake."""
    assert check_scene(scene(obstacles=[{"x": 4.8, "y": 0, "w": 1, "d": 2}])) == []


def test_a_route_whose_leg_crosses_a_box_needs_a_waypoint():
    wall = [{"x": 2, "y": 0, "w": 0.3, "d": 4}]
    blocked = scene(obstacles=wall, routes={"dock->shelf": []})
    (warning,) = check_scene(blocked)
    assert warning.code == "leg_crosses_obstacle" and warning.where == [(0.0, 0.0), (4.0, 0.0)]
    assert warning.message == (
        "route dock->shelf: the leg from dock to shelf crosses obstacle 0 (a 0.3 x 4 m box at 2, 0): add a waypoint"
    )
    around = {
        "dock": {"pose": {"x": 0, "y": 0}},
        "past_the_wall": {"pose": {"x": 2, "y": 3}},
        "shelf": {"pose": {"x": 4, "y": 0}},
    }
    assert check_scene(scene(locations=around, obstacles=wall, routes={"dock->shelf": ["past_the_wall"]})) == []


def test_a_leg_that_brushes_a_box_is_a_crossing_for_a_robot_with_a_body():
    """The line misses the box by less than the robot's half width."""
    box = [{"x": 2, "y": 0.5 + CLEARANCE_M - 0.05, "w": 1, "d": 1}]
    assert codes(check_scene(scene(obstacles=box, routes={"dock->shelf": []}))) == ["leg_crosses_obstacle"]
    clear = [{"x": 2, "y": 0.5 + CLEARANCE_M + 0.05, "w": 1, "d": 1}]
    assert check_scene(scene(obstacles=clear, routes={"dock->shelf": []})) == []


@pytest.mark.parametrize(
    ("p", "q", "hits"),
    [
        ((0, 0), (4, 0), True),  # straight through
        ((0, 0), (1, 0), False),  # stops short
        ((0, 2), (4, 2), False),  # passes beside
        ((2, -3), (2, 3), True),  # along the other axis
        ((0, -1.01), (4, -1.01), False),  # parallel, just outside
        ((0, 3), (4, -3), True),  # diagonal through
        ((0, 3), (1.4, 1.1), False),  # diagonal that ends before it
        ((2, 0), (2, 0), True),  # a point inside
        ((9, 9), (9, 9), False),  # a point outside
    ],
)
def test_segment_against_a_box(p, q, hits):
    assert segment_hits_box(p, q, (1.5, -1.0, 2.5, 1.0)) is hits


# --- what the camera sees ----------------------------------------------------------------------------------------
def test_an_object_nobody_looks_at_says_who_is_nearest_and_why_not():
    behind = scene(objects={"gauge": {"cls": "gauge", "pose": {"x": 4, "y": 2, "z": 0.5}}})
    (warning,) = check_scene(behind)
    assert warning.code == "object_out_of_view" and warning.where == [(4.0, 2.0)]
    assert warning.message == (
        "object 'gauge' is in view from no location: the nearest, 'shelf', is 2.0 m away but outside the 40° either "
        f"side of the heading: yaw {math.pi / 2:.2f} would face it"
    )
    far = scene(objects={"gauge": {"cls": "gauge", "pose": {"x": 8, "y": 0, "z": 0.5}}})
    assert check_scene(far)[0].message.endswith("is 4.0 m away, and the camera reads up to 3 m")


def test_an_object_inside_a_box_is_hidden_from_a_camera():
    """Nothing collides with an object, so a scene can bury one in a shelf; the scene's truth would still find it."""
    shelf = [{"x": 5, "y": 0, "w": 0.6, "d": 2, "h": 1.8}]
    buried = scene(obstacles=shelf, objects={"box": {"cls": "box", "pose": {"x": 4.9, "y": 0, "z": 1.0}}})
    (warning,) = check_scene(buried)
    assert warning.code == "object_inside_obstacle" and warning.where == [(4.9, 0.0)]
    assert warning.message == (
        "object 'box' is inside obstacle 0 (a 0.6 x 2 m box at 5, 0), which is 1.8 m high: a camera sees the box, "
        "not the object. Put it on top (z above 1.8) or in front of it"
    )
    on_top = scene(obstacles=shelf, objects={"box": {"cls": "box", "pose": {"x": 4.9, "y": 0, "z": 1.85}}})
    in_front = scene(obstacles=shelf, objects={"box": {"cls": "box", "pose": {"x": 4.6, "y": 0, "z": 1.0}}})
    assert check_scene(on_top) == [] and check_scene(in_front) == []


def test_an_object_in_front_of_a_stop_or_without_a_known_position_is_fine():
    seen = scene(objects={"box": {"cls": "box", "pose": {"x": 5, "y": 0.5, "z": 1}}, "lost": {"cls": "key"}})
    assert check_scene(seen) == []


def test_a_stop_that_looks_at_something_wants_a_landmark_when_the_scene_has_them():
    box = {"box": {"cls": "box", "pose": {"x": 5, "y": 0, "z": 1}}}
    behind = [{"marker_id": 1, "x": 2, "y": 0, "facing": 0.0, "size_m": 0.3}]  # behind the robot at the shelf
    (warning,) = check_scene(scene(objects=box, landmarks=behind))
    assert warning.code == "stop_without_landmark" and warning.message.startswith("location 'shelf' looks at")
    ahead = [{"marker_id": 1, "x": 6.5, "y": 1, "facing": 3.14, "size_m": 0.3}]
    assert check_scene(scene(objects=box, landmarks=ahead)) == []
    too_far_for_its_size = [{"marker_id": 1, "x": 6.5, "y": 1, "facing": 3.14, "size_m": 0.2}]
    assert codes(check_scene(scene(objects=box, landmarks=too_far_for_its_size))) == ["stop_without_landmark"]
    assert check_scene(scene(objects=box)) == []  # no landmarks: the scene is not meant for drifting odometry


# --- a plan in its scene -----------------------------------------------------------------------------------------
WALL = [{"x": 2, "y": 0, "w": 0.3, "d": 4}]
AROUND = {
    "dock": {"pose": {"x": 0, "y": 0}},
    "past_the_wall": {"pose": {"x": 2, "y": 3}},
    "shelf": {"pose": {"x": 4, "y": 0}},
}


def test_a_navigate_through_a_wall_is_said_before_a_run_is_spent_on_it():
    walled = scene(locations=AROUND, obstacles=WALL)
    (warning,) = check_plan(plan({"skill": "navigate", "params": {"to": "shelf"}}), walled)
    assert warning.code == "leg_crosses_obstacle" and warning.where == [(0.0, 0.0), (4.0, 0.0)]
    assert warning.message == (
        "step 0 (navigate): the leg from the start to shelf crosses obstacle 0 (a 0.3 x 4 m box at 2, 0): "
        "add a waypoint with via"
    )
    with_via = plan({"skill": "navigate", "params": {"to": "shelf", "via": ["past_the_wall"]}})
    assert check_plan(with_via, walled) == []


def test_the_robot_is_followed_from_leg_to_leg():
    walled = scene(locations=AROUND, obstacles=WALL)
    there_and_back = plan(
        {"skill": "navigate", "params": {"to": "shelf", "via": ["past_the_wall"]}},
        {"skill": "say", "params": {"text": "here"}},
        {"skill": "navigate", "params": {"to": "dock"}},
    )
    (warning,) = check_plan(there_and_back, walled)
    assert warning.message.startswith("step 2 (navigate): the leg from shelf to dock crosses obstacle 0")


def test_an_inspection_from_where_nothing_is_in_view():
    gauge = {"gauge_01": {"cls": "gauge", "pose": {"x": 4, "y": 2, "z": 0.5}}}
    looking_away = scene(objects=gauge)
    inspect = plan({"skill": "inspect", "params": {"target": "shelf", "checks": ["present:gauge", "absent:leak"]}})
    (warning,) = check_plan(inspect, looking_away)
    assert warning.code == "nothing_in_view" and warning.where == [(4.0, 0.0), (4.0, 2.0)]
    assert warning.message == (
        "step 0 (inspect): from 'shelf' nothing of class 'gauge' is in view: 'gauge_01' is 2.0 m away but outside "
        "the 40° either side of the heading: yaw 1.57 would face it"
    )
    facing = {**AROUND, "shelf": {"pose": {"x": 4, "y": 0, "yaw": 1.57}}}
    assert check_plan(inspect, scene(locations=facing, objects=gauge)) == []
    missing = plan({"skill": "inspect", "params": {"target": "shelf", "checks": ["present:valve"]}})
    assert check_plan(missing, looking_away)[0].message.endswith("the scene has no 'valve' with a known position")


def test_a_detect_looks_from_where_the_robot_stands():
    box = {"box_1": {"cls": "box", "pose": {"x": 5, "y": 0, "z": 1}}}
    world = scene(objects=box)
    from_the_start = plan({"skill": "detect", "params": {"cls": "box"}})
    assert check_plan(from_the_start, world)[0].message.startswith(
        "step 0 (detect): from the start nothing of class 'box' is in view: 'box_1' is 5.0 m away"
    )
    at_the_shelf = plan({"skill": "navigate", "params": {"to": "shelf"}}, {"skill": "detect", "params": {"cls": "box"}})
    assert check_plan(at_the_shelf, world) == []


def test_what_is_only_known_at_run_time_is_left_to_the_run():
    """A reference to an earlier step's evidence, or a name the scene lacks (validate_plan's to refuse)."""
    world = scene(obstacles=WALL)
    unknown = plan(
        {"skill": "detect", "params": {"cls": "box"}},
        {"skill": "navigate", "params": {"to": "$detect.objects[0].id"}},
        {"skill": "navigate", "params": {"to": "nowhere"}},
        {"skill": "inspect", "params": {"target": "nowhere", "checks": ["present:box"]}},
    )
    assert codes(check_plan(unknown, world)) == ["nothing_in_view"]  # only the detect, from the start


# --- the samples -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("plan_name", "scene_name"),
    [("yard_patrol", "yard_outdoor"), ("demo_material_runner", "warehouse_small"), ("test_track", "test_track")],
)
def test_the_samples_that_work_are_clean(plan_name, scene_name):
    sample = load_scene(bundled_scenes()[scene_name])
    assert check_scene(sample) == []
    assert check_plan(load_plan(bundled_plans()[plan_name]), sample) == []


def test_the_samples_made_to_fail_say_how():
    fenced = check_scene(load_scene(bundled_scenes()["lab_geofence"]))
    assert codes(fenced) == ["outside_geofence"] and "workstation_B" in fenced[0].message
    blocked = load_scene(bundled_scenes()["lab_blocked"])
    crossing = check_plan(load_plan("tests/golden/plans/to_workstation.yaml"), blocked)
    assert codes(crossing) == ["leg_crosses_obstacle"]
