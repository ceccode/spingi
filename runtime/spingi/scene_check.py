"""Geometry checks on a scene, and on a plan in its scene (ADR-0028).

The schema says whether a file is a scene. These checks say whether the scene is one a robot can work in: what the
tutorial tells people to look at, checked before a run is spent finding it out. Plane geometry only, on the same
rules the runtime uses: the camera's view (`perception.view`), the landmarks `navigate` expects (`core.localization`).

A warning does not stop anything. A wall across the way is a mistake in most scenes and the point of `lab_blocked`.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field

from spingi.core.localization import in_view
from spingi.core.plan import TaskPlan, parse_ref
from spingi.core.types import Landmark, Pose2D
from spingi.perception.view import HEAD_CAMERA
from spingi.scenes import SceneFile, SceneObstacle, ScenePose
from spingi.skills.navigate import LANDMARK_HALF_FOV_RAD, TAG_READ_RANGE

CLEARANCE_M = 0.3  # half a walking robot's width and a little more: a leg nearer than this to a box touches it
TOUCH_M = 0.15  # a place nearer than this to a box has the robot's body in it; stops stand 0.3 m from tables
FENCE_MARGIN_M = 0.3  # a walking robot stops this far off its target on a bad day: nearer to the fence, it leaves it

WarningCode = Literal[
    "outside_geofence",
    "near_geofence",
    "inside_obstacle",
    "near_obstacle",
    "leg_crosses_obstacle",
    "object_out_of_view",
    "stop_without_landmark",
    "nothing_in_view",
]
Point = tuple[float, float]


class SceneWarning(BaseModel):
    code: WarningCode
    message: str
    where: list[Point] = Field(default_factory=list, description="points to mark on a map: a place, or a leg's ends")


def check_scene(scene: SceneFile) -> list[SceneWarning]:
    """What a robot would trip over in this scene, most serious first within each kind."""
    return [
        *_fence(scene),
        *_places_in_obstacles(scene),
        *_routes(scene),
        *_objects_nobody_sees(scene),
        *_stops_without_landmarks(scene),
    ]


def check_plan(plan: TaskPlan, scene: SceneFile) -> list[SceneWarning]:
    """What this plan would meet in this scene: legs that cross an obstacle, looks that have nothing in view.

    The plan is taken as valid (`validate_plan`): a step that names an unknown location, or takes a parameter from
    an earlier step's evidence, is skipped here. The robot is followed from its start through every `navigate`.
    """
    warnings: list[SceneWarning] = []
    at_name, at = "the start", scene.robot.pose
    for i, step in enumerate(plan.steps):
        params = step.params
        if step.skill == "navigate":
            names = [*_names(params.get("via")), *_names(params.get("to"))]
            if not names or any(n not in scene.locations for n in names):
                continue
            for name in names:
                target = scene.locations[name].pose
                hit = _first_obstacle_on(scene, at, target)
                if hit is not None:
                    leg = f"the leg from {at_name} to {name} crosses {_obstacle(*hit)}"
                    text = f"step {i} (navigate): {leg}: add a waypoint with via"
                    warnings.append(_warn("leg_crosses_obstacle", text, at, target))
                at_name, at = name, target
        elif step.skill == "inspect":
            (name,) = _names(params.get("target")) or [None]
            if name not in scene.locations:
                continue
            pose = scene.locations[name].pose
            wanted = [c.removeprefix("present:") for c in params.get("checks") or [] if _is_present_check(c)]
            warnings += [_nothing_in_view(scene, i, "inspect", cls, name, pose) for cls in wanted]
        elif step.skill == "detect" and isinstance(params.get("cls"), str) and parse_ref(params["cls"]) is None:
            warnings.append(_nothing_in_view(scene, i, "detect", params["cls"], at_name, at))
    return [w for w in warnings if w is not None]


# --- the scene ---------------------------------------------------------------------------------------------------
def _places(scene: SceneFile) -> list[tuple[str, ScenePose]]:
    return [
        ("the robot's start", scene.robot.pose),
        *((f"location '{n}'", loc.pose) for n, loc in scene.locations.items()),
    ]


def _fence(scene: SceneFile) -> list[SceneWarning]:
    fence = scene.safety.geofence
    if fence is None:
        return []
    stops = "an emergency stop" if scene.safety.estop_on_geofence else "a stop"
    out = []
    for label, pose in _places(scene):
        margin = min(pose.x - fence.x_min, fence.x_max - pose.x, pose.y - fence.y_min, fence.y_max - pose.y)
        if margin < 0:
            text = f"{label} is outside the geofence: reaching it is {stops}"
            out.append(_warn("outside_geofence", text, pose))
        elif margin < FENCE_MARGIN_M:
            text = f"{label} is {margin:.2f} m from the edge of the geofence: a robot that overshoots gets {stops}"
            out.append(_warn("near_geofence", text, pose))
    return out


def _places_in_obstacles(scene: SceneFile) -> list[SceneWarning]:
    out = []
    for label, pose in _places(scene):
        for index, obstacle in enumerate(scene.obstacles):
            gap = _distance_to_box((pose.x, pose.y), obstacle)
            if gap == 0:
                out.append(_warn("inside_obstacle", f"{label} is inside {_obstacle(index, obstacle)}", pose))
            elif gap < TOUCH_M:
                text = f"{label} is {gap:.2f} m from {_obstacle(index, obstacle)}: the robot does not fit"
                out.append(_warn("near_obstacle", text, pose))
    return out


def _routes(scene: SceneFile) -> list[SceneWarning]:
    out = []
    for key, waypoints in scene.routes.items():
        src, _, dst = key.partition("->")
        names = [src, *waypoints, dst]
        for a, b in pairwise(names):
            start, end = scene.locations[a].pose, scene.locations[b].pose
            hit = _first_obstacle_on(scene, start, end)
            if hit is not None:
                text = f"route {key}: the leg from {a} to {b} crosses {_obstacle(*hit)}: add a waypoint"
                out.append(_warn("leg_crosses_obstacle", text, start, end))
    return out


def _objects_nobody_sees(scene: SceneFile) -> list[SceneWarning]:
    out = []
    for oid, obj in scene.objects.items():
        if obj.pose is None or not scene.locations:
            continue
        x, y = obj.pose.x, obj.pose.y
        if any(_sees(loc.pose, x, y) for loc in scene.locations.values()):
            continue
        name, pose = min(((n, loc.pose) for n, loc in scene.locations.items()), key=lambda item: _dist(item[1], x, y))
        out.append(
            _warn("object_out_of_view", f"object '{oid}' is in view from no location: {_why(name, pose, x, y)}", (x, y))
        )
    return out


def _stops_without_landmarks(scene: SceneFile) -> list[SceneWarning]:
    """Only in a scene that has landmarks, so one meant for drifting odometry, and only where the robot stops to
    look at something: an inspection from half a metre off is the failure the landmarks are there to prevent."""
    if not scene.landmarks:
        return []
    landmarks = scene.world().landmarks
    out = []
    for name, loc in scene.locations.items():
        looks_at_something = any(
            o.pose is not None and _sees(loc.pose, o.pose.x, o.pose.y) for o in scene.objects.values()
        )
        if looks_at_something and not any(_reads(loc.pose, lm) for lm in landmarks):
            text = (
                f"location '{name}' looks at an object and has no landmark in view: with drifting odometry "
                f"(--drift, --realistic) the robot cannot correct its pose there. A tag is read up to "
                f"{TAG_READ_RANGE:g} times its side away, within 45° of the heading"
            )
            out.append(_warn("stop_without_landmark", text, loc.pose))
    return out


# --- the plan ----------------------------------------------------------------------------------------------------
def _nothing_in_view(
    scene: SceneFile, step: int, skill: str, cls: str, place: str, pose: ScenePose
) -> SceneWarning | None:
    candidates = [(oid, o.pose) for oid, o in scene.objects.items() if o.cls == cls and o.pose is not None]
    if any(_sees(pose, p.x, p.y) for _, p in candidates):
        return None
    head = f"step {step} ({skill}): from {_quoted(place)} nothing of class '{cls}' is in view"
    if not candidates:
        return _warn("nothing_in_view", f"{head}: the scene has no '{cls}' with a known position", pose)
    oid, p = min(candidates, key=lambda item: _dist(pose, item[1].x, item[1].y))
    return _warn("nothing_in_view", f"{head}: '{oid}' is {_out_of_view(pose, p.x, p.y)}", pose, (p.x, p.y))


def _names(value: object) -> list[str]:
    """Location names given to a parameter; a reference to an earlier step's evidence is not a name."""
    values = value if isinstance(value, list) else [value]
    return [v for v in values if isinstance(v, str) and v and parse_ref(v) is None]


def _is_present_check(check: object) -> bool:
    return isinstance(check, str) and check.startswith("present:") and len(check) > len("present:")


# --- geometry ----------------------------------------------------------------------------------------------------
def _sees(pose: ScenePose, x: float, y: float) -> bool:
    return HEAD_CAMERA.sees(Pose2D(x=pose.x, y=pose.y, yaw=pose.yaw), x, y)


def _reads(pose: ScenePose, landmark: Landmark) -> bool:
    here = Pose2D(x=pose.x, y=pose.y, yaw=pose.yaw)
    return in_view(here, landmark, TAG_READ_RANGE * landmark.size_m, LANDMARK_HALF_FOV_RAD)


def _dist(pose: ScenePose, x: float, y: float) -> float:
    return math.hypot(x - pose.x, y - pose.y)


def _out_of_view(pose: ScenePose, x: float, y: float) -> str:
    """Why a point is not in the camera's view from a pose, with the heading that would face it."""
    distance = _dist(pose, x, y)
    if distance > HEAD_CAMERA.max_range_m:
        return f"{distance:.1f} m away, and the camera reads up to {HEAD_CAMERA.max_range_m:g} m"
    facing = math.atan2(y - pose.y, x - pose.x)
    return f"{distance:.1f} m away but outside the 40° either side of the heading: yaw {facing:.2f} would face it"


def _why(name: str, pose: ScenePose, x: float, y: float) -> str:
    return f"the nearest, '{name}', is {_out_of_view(pose, x, y)}"


def _quoted(place: str) -> str:
    return place if place == "the start" else f"'{place}'"


def _box(o: SceneObstacle, margin: float = 0.0) -> tuple[float, float, float, float]:
    return o.x - o.w / 2 - margin, o.y - o.d / 2 - margin, o.x + o.w / 2 + margin, o.y + o.d / 2 + margin


def _distance_to_box(p: Point, o: SceneObstacle) -> float:
    """From a point to a box's footprint; 0 inside it."""
    x0, y0, x1, y1 = _box(o)
    return math.hypot(max(x0 - p[0], 0.0, p[0] - x1), max(y0 - p[1], 0.0, p[1] - y1))


def _first_obstacle_on(scene: SceneFile, a: ScenePose, b: ScenePose) -> tuple[int, SceneObstacle] | None:
    """The first obstacle a robot walking straight from `a` to `b` would touch, if any.

    A place that stands close to a box on purpose, a stop in front of a shelf, is not a crossing: the leg is checked
    against the box itself there, and against the box with the robot's clearance everywhere else.
    """
    for index, obstacle in enumerate(scene.obstacles):
        ends_close = any(_distance_to_box((p.x, p.y), obstacle) < CLEARANCE_M for p in (a, b))
        if segment_hits_box((a.x, a.y), (b.x, b.y), _box(obstacle, 0.0 if ends_close else CLEARANCE_M)):
            return index, obstacle
    return None


def segment_hits_box(p: Point, q: Point, box: tuple[float, float, float, float]) -> bool:
    """Whether the segment from `p` to `q` touches an axis-aligned box (x0, y0, x1, y1). Liang-Barsky clipping."""
    x0, y0, x1, y1 = box
    dx, dy = q[0] - p[0], q[1] - p[1]
    t_in, t_out = 0.0, 1.0
    for delta, low, high in ((dx, x0 - p[0], x1 - p[0]), (dy, y0 - p[1], y1 - p[1])):
        if delta == 0:
            if low > 0 or high < 0:
                return False  # parallel to this pair of sides, and outside them
            continue
        t0, t1 = low / delta, high / delta
        t_in, t_out = max(t_in, min(t0, t1)), min(t_out, max(t0, t1))
        if t_in > t_out:
            return False
    return True


def _obstacle(index: int, o: SceneObstacle) -> str:
    return f"obstacle {index} (a {o.w:g} x {o.d:g} m box at {o.x:g}, {o.y:g})"


def _warn(code: WarningCode, message: str, *points: ScenePose | Point) -> SceneWarning:
    where = [(p.x, p.y) if isinstance(p, ScenePose) else p for p in points]
    return SceneWarning(code=code, message=message, where=where)
