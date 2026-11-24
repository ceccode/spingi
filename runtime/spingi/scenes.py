"""Scenes as data: the file a place is described in, and the initial WorldState it gives (ADR-0004, ADR-0028).

A scene file has one schema, `SceneFile`, and every reader goes through it. A key nobody declares is an error, not a
section dropped in silence: `obstacle:` for `obstacles:` would leave a wall out of the world and the robot would walk
through it. The simulator's builders check the same values again before they write them into the model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from spingi.core.types import NAME_PATTERN, Landmark, Location, ObjectRef, Pose2D, Pose3D, RobotState, WorldState
from spingi.safety.limits import SafetyLimits

# Limits the simulator's builders share (sim_mujoco/scene.py, terrain.py): one place for each number.
MAX_MARKER_ID = 63  # tag36h11 images shipped: ids 0..63
SURFACE_NAMES = ("floor", "concrete", "asphalt", "gravel", "dirt", "grass")
MIN_CELL_M, MAX_CELL_M, DEFAULT_CELL_M = 0.08, 0.25, 0.1
MAX_SAMPLES = 2_000_000  # a larger terrain grid is a mistake in the scene, not a terrain
MAX_HEIGHT_M = 3.0  # built structures stand on the ground: from 0 to 3 m, no pits
MAX_ROUGHNESS_M = 0.1
MAX_LANDMARK_HEIGHT_M, MAX_TAG_SIZE_M = 3.0, 0.6

Name = Annotated[str, Field(pattern=NAME_PATTERN)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$", description="#rrggbb")]
Surface = Literal["floor", "concrete", "asphalt", "gravel", "dirt", "grass"]
Span = Annotated[list[float], Field(min_length=2, max_length=2, description="[from, to] in metres, from < to")]
Height = Annotated[float, Field(ge=0, le=MAX_HEIGHT_M)]


class SceneError(ValueError):
    """The scene file contains something a scene cannot hold. `errors` has one line per problem."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors if errors is not None else [message]


class _Part(BaseModel):
    """A part of a scene file: an unknown key is an error, a number is a finite number (ADR-0028)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _empty_keys_take_their_default(cls, data: Any) -> Any:
        """`regions:` with nothing under it is YAML for null: the same as leaving the key out."""
        return {k: v for k, v in data.items() if v is not None} if isinstance(data, dict) else data


class ScenePose(_Part):
    x: float
    y: float
    yaw: float = Field(default=0.0, description="rad, counter-clockwise from +x")


class ScenePosition(_Part):
    x: float
    y: float
    z: float = Field(description="height of the object's centre, metres")


class SceneRobot(_Part):
    pose: ScenePose = Field(default_factory=lambda: ScenePose(x=0.0, y=0.0), description="where the robot starts")
    battery_pct: float = Field(default=100.0, ge=0, le=100)


class SceneLocation(_Part):
    """A named place: the only places a plan can send the robot to."""

    pose: ScenePose
    tolerance_m: float = Field(default=0.15, gt=0, description="how close counts as arrived")


class SceneObstacle(_Part):
    """A solid box standing on the ground, aligned with the axes. The robot collides with it."""

    x: float = Field(description="centre")
    y: float = Field(description="centre")
    w: float = Field(default=0.5, gt=0, description="size along x")
    d: float = Field(default=0.5, gt=0, description="size along y")
    h: float = Field(default=1.0, gt=0, description="height")
    color: Color | None = None


class SceneObject(_Part):
    """Something the robot looks for with its camera. No collision."""

    cls: Name = Field(description="the class plans look for, as in present:<cls>")
    marker_id: int | None = Field(default=None, ge=0, le=MAX_MARKER_ID, description="its AprilTag, if it has one")
    size: Annotated[list[Annotated[float, Field(gt=0)]], Field(min_length=3, max_length=3)] = Field(
        default_factory=lambda: [0.15, 0.1, 0.1], description="[along x, along y, height] in metres"
    )
    pose: ScenePosition | None = Field(default=None, description="the object's centre; unknown when left out")
    color: Color | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class SceneLandmark(_Part):
    """An AprilTag at a known place, on a post or flat on a wall: reading it corrects the robot's pose."""

    marker_id: int = Field(ge=0, le=MAX_MARKER_ID)
    x: float
    y: float
    facing: float = Field(default=0.0, description="the direction the tag's face looks, rad")
    height_m: float = Field(default=0.6, gt=0, le=MAX_LANDMARK_HEIGHT_M, description="the tag's centre above ground")
    size_m: float = Field(default=0.2, gt=0, le=MAX_TAG_SIZE_M, description="side of the tag's black square")
    post: bool = Field(default=True, description="false: flat on a wall or an obstacle, no post drawn")


class SceneExtent(_Part):
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @model_validator(mode="after")
    def _ordered(self) -> SceneExtent:
        if not (self.x_min < self.x_max and self.y_min < self.y_max):
            raise ValueError("x_min must be less than x_max and y_min less than y_max")
        return self


class _Area(_Part):
    x: Span
    y: Span

    @model_validator(mode="after")
    def _ordered(self) -> _Area:
        for axis in ("x", "y"):
            a, b = getattr(self, axis)
            if not a < b:
                raise ValueError(f"{axis} must go from the smaller to the larger value, got [{a}, {b}]")
        return self


class TerrainRegion(_Area):
    """A rectangle of another surface. Where two overlap, the last one listed wins."""

    surface: Surface
    roughness_m: float | None = Field(default=None, ge=0, le=MAX_ROUGHNESS_M, description="instead of the surface's")


class TerrainRamp(_Area):
    """A solid slope between two heights."""

    from_m: Height = 0.0
    to_m: Height
    along: Literal["x", "y", "-x", "-y"] = Field(default="x", description="the direction it rises in")

    @model_validator(mode="after")
    def _walkable(self) -> TerrainRamp:
        (x0, x1), (y0, y1) = self.x, self.y
        length = (x1 - x0) if self.along.endswith("x") else (y1 - y0)
        if length > 0 and abs(self.to_m - self.from_m) / length > 1.0:
            raise ValueError("steeper than 45 degrees: make it longer or use a raised area")
        return self


class TerrainRaised(_Area):
    """A raised area, flat on top with vertical sides: a kerb, a step, a loading dock."""

    height_m: Height


class SceneTerrain(_Part):
    """The ground, outdoors: a base surface, rectangles of other surfaces, ramps and raised areas."""

    extent: SceneExtent = Field(description="must cover the safety geofence")
    cell_m: float = Field(default=DEFAULT_CELL_M, ge=MIN_CELL_M, le=MAX_CELL_M)
    seed: int = Field(default=0, ge=0, lt=2**31, description="same seed, same bumps on every run")
    surface: Surface = "floor"
    regions: list[TerrainRegion] = Field(default_factory=list)
    ramps: list[TerrainRamp] = Field(default_factory=list)
    raised: list[TerrainRaised] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_too_large(self) -> SceneTerrain:
        e = self.extent
        if ((e.x_max - e.x_min) / self.cell_m + 1) * ((e.y_max - e.y_min) / self.cell_m + 1) > MAX_SAMPLES:
            raise ValueError(f"grid larger than {MAX_SAMPLES} samples: use a larger cell_m or a smaller extent")
        return self


class SceneFile(_Part):
    """A scene: the world the robot knows. Distances in metres on the ground plane, angles in radians."""

    robot: SceneRobot = Field(default_factory=SceneRobot)
    locations: dict[Name, SceneLocation] = Field(default_factory=dict)
    obstacles: list[SceneObstacle] = Field(default_factory=list)
    objects: dict[Name, SceneObject] = Field(default_factory=dict)
    landmarks: list[SceneLandmark] = Field(default_factory=list)
    routes: dict[str, list[Name]] = Field(
        default_factory=dict, description='"from->to": the waypoints that keep that leg clear of obstacles'
    )
    safety: SafetyLimits = Field(default_factory=SafetyLimits)
    terrain: SceneTerrain | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SceneFile:
        problems = [*self._tag_clashes(), *self._route_problems(), *self._uncovered_fence()]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _tag_clashes(self) -> list[str]:
        """A marker id names one tag in the scene, objects and landmarks together."""
        owners: dict[int, str] = {}
        problems = []
        tagged = [(o.marker_id, name) for name, o in self.objects.items() if o.marker_id is not None]
        tagged += [(lm.marker_id, f"landmark {i}") for i, lm in enumerate(self.landmarks)]
        for marker_id, owner in tagged:
            if marker_id in owners:
                problems.append(f"marker_id {marker_id} is used by both {owners[marker_id]} and {owner}")
            owners.setdefault(marker_id, owner)
        return problems

    def _route_problems(self) -> list[str]:
        problems = []
        for key, waypoints in self.routes.items():
            src, sep, dst = key.partition("->")
            if not sep or not all(re.match(NAME_PATTERN, n) for n in (src, dst)):
                problems.append(f"routes: invalid route {key!r}: expected 'from->to' with location names")
                continue
            unknown = [n for n in (src, dst, *waypoints) if n not in self.locations]
            if unknown:
                problems.append(f"routes: {key!r} names {', '.join(repr(n) for n in unknown)}, not a location here")
        return problems

    def _uncovered_fence(self) -> list[str]:
        """A robot inside the geofence must always have ground under it."""
        fence, terrain = self.safety.geofence, self.terrain
        if fence is None or terrain is None:
            return []
        e = terrain.extent
        inside = e.x_min <= fence.x_min and fence.x_max <= e.x_max and e.y_min <= fence.y_min and fence.y_max <= e.y_max
        return [] if inside else ["terrain.extent must cover the safety geofence: the robot would walk off the ground"]

    def world(self) -> WorldState:
        """The world as the robot knows it when a run starts."""
        return WorldState(
            locations={
                name: Location(name=name, pose=_pose(loc.pose), tolerance_m=loc.tolerance_m)
                for name, loc in self.locations.items()
            },
            objects={
                oid: ObjectRef(
                    id=oid,
                    cls=obj.cls,
                    marker_id=obj.marker_id,
                    pose=Pose3D(x=obj.pose.x, y=obj.pose.y, z=obj.pose.z) if obj.pose else None,
                    confidence=obj.confidence,
                )
                for oid, obj in self.objects.items()
            },
            landmarks=[
                Landmark(
                    marker_id=lm.marker_id,
                    pose=Pose2D(x=lm.x, y=lm.y, yaw=lm.facing),
                    height_m=lm.height_m,
                    size_m=lm.size_m,
                )
                for lm in self.landmarks
            ],
            robot=RobotState(pose=_pose(self.robot.pose), battery_pct=self.robot.battery_pct),
        )


def _pose(pose: ScenePose) -> Pose2D:
    return Pose2D(x=pose.x, y=pose.y, yaw=pose.yaw)


# --- reading -----------------------------------------------------------------------------------------------------
def scene_errors(raw: Any) -> list[str]:
    """What is wrong with a scene given as parsed YAML, one line per problem; empty when it is a scene."""
    if raw is None:
        raw = {}  # an empty file: a scene with nothing in it
    if not isinstance(raw, dict):
        return ["a scene is a mapping with sections such as locations, obstacles, objects"]
    try:
        SceneFile.model_validate(raw)
    except ValidationError as exc:
        return _lines(exc)
    return []


def parse_scene(raw: Any, source: str = "") -> SceneFile:
    """A scene from parsed YAML. Raises `SceneError` with every problem, in one line."""
    errors = scene_errors(raw)
    if errors:
        where = f" ({source})" if source else ""
        raise SceneError(f"invalid scene{where}: {'; '.join(errors)}", errors)
    return SceneFile.model_validate(raw if raw is not None else {})


def read_scene_yaml(text: str, source: str = "") -> Any:
    """Parsed YAML of a scene, with a YAML mistake reported as a `SceneError` in one line."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        where = f" ({source})" if source else ""
        problem = " ".join(str(exc).split())
        raise SceneError(f"invalid scene{where}: not valid YAML: {problem}", [f"not valid YAML: {problem}"]) from exc


def load_scene(path: Path | str) -> SceneFile:
    return parse_scene(read_scene_yaml(Path(path).read_text(encoding="utf-8"), str(path)), str(path))


def load_world(path: Path | str) -> WorldState:
    return load_scene(path).world()


def load_safety_limits(path: Path | str) -> SafetyLimits:
    """The optional `safety:` section of a scene file; defaults when absent."""
    return load_scene(path).safety


def load_routes(path: Path | str) -> dict[str, list[str]]:
    """The optional `routes:` section: "from->to" -> waypoints, given to the planner as a hint."""
    return dict(load_scene(path).routes)


def parse_landmarks(raw: dict) -> list[Landmark]:
    """The optional `landmarks:` section of a scene given as a mapping: tags at known places,
    `{marker_id, x, y, facing, height_m, size_m}`, with `facing` the direction the tag's face looks, in radians.
    A marker id names one landmark."""
    out: list[Landmark] = []
    for i, lm in enumerate(raw.get("landmarks") or []):
        if not isinstance(lm, dict) or "marker_id" not in lm:
            raise ValueError(f"landmark {i}: expected a mapping with marker_id, x, y, facing")
        sizes = {k: lm[k] for k in ("height_m", "size_m") if k in lm}
        out.append(
            Landmark(marker_id=lm["marker_id"], pose=Pose2D(x=lm["x"], y=lm["y"], yaw=lm.get("facing", 0.0)), **sizes)
        )
    ids = [lm.marker_id for lm in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"landmarks: a marker id is used twice in {ids}")
    return out


def describe_world(state: WorldState, routes: dict[str, list[str]] | None = None) -> str:
    lines = ["Locations (name: x, y in metres):"]
    for name, loc in state.locations.items():
        lines.append(f"- {name}: {loc.pose.x:.2f}, {loc.pose.y:.2f}")
    lines.append("Known objects (id, class, position):")
    if state.objects:
        for oid, obj in state.objects.items():
            where = f"{obj.pose.x:.2f}, {obj.pose.y:.2f}, {obj.pose.z:.2f}" if obj.pose else "position unknown"
            lines.append(f"- {oid}, class {obj.cls}, at {where}")
    else:
        lines.append("- none")
    lines.append(
        f"Robot: at {state.robot.pose.x:.2f}, {state.robot.pose.y:.2f}, battery {state.robot.battery_pct:.0f}%"
    )
    lines.append(f"Holding: {state.robot.holding.id if state.robot.holding else 'nothing'}")
    if routes:
        lines.append("Routes (from->to: waypoints to pass through, in order):")
        for key, via in routes.items():
            lines.append(f"- {key}: {', '.join(via)}")
    return "\n".join(lines)


def json_schema() -> dict[str, Any]:
    """The schema of a scene file, published in docs/schemas/scene.schema.json."""
    return SceneFile.model_json_schema()


def _lines(exc: ValidationError) -> list[str]:
    """One line per error, `where: what`. A check of the whole scene has no `where` and may hold several problems."""
    lines: list[str] = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"] if p != "[key]")
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "value_error" and not where:
            lines.extend(message.split("; "))
        else:
            lines.append(f"{where}: {message}" if where else message)
    return lines
