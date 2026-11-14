"""Generates the MJCF of a scene from the declarative YAML (ADR-0004).

The scene includes the robot model (G1 by default, see `spingi.robots`) and adds the floor or, with a `terrain`
section, the ground (`terrain.py`, ADR-0015), locations (visual discs), obstacles (boxes with collision, standing
on the ground) and objects (boxes without collision in v0). An object with a `marker_id` carries its AprilTag on
its four side faces, so the frames the head camera renders can be read by `MarkerPerceiver` like a real camera's
(M4.0). Landmarks are tags at known places (ADR-0016), on a post or, with `post: false`, flat on a wall or an
obstacle; they are drawn but have no collision, so a scene keeps them off the routes. The generated file goes into
the model folder because `<include>` and `meshdir` resolve relative to the main file.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from spingi.adapters.sim_mujoco.terrain import (
    SURFACES,
    TerrainMap,
    TerrainSpec,
    build_terrain,
    contact_attrs,
    parse_terrain,
    viewer_copy,
)
from spingi.adapters.sim_mujoco.terrain import mjcf as terrain_mjcf
from spingi.adapters.sim_mujoco.values import SceneError, color, name, num
from spingi.core.types import Landmark
from spingi.scenes import parse_landmarks

# Resolved from this file, never from the working directory: a folder you run `spingi` in must not supply the robot.
MODELS_DIR = Path(__file__).resolve().parents[3] / "sim" / "models"
DEFAULT_MODEL_DIR = MODELS_DIR / "unitree_g1"
MARKERS_DIR = Path(__file__).resolve().parents[3] / "sim" / "markers"  # tag36h11_NN.png, ids 0..63, committed

# A tag image is the 10x10-cell AprilTag (8 data cells and the black border) plus a 1-cell white quiet zone on each
# side: 12 cells. The decal is a square of `MARKER_DECAL_SCALE` times the object's smallest extent; the black square
# the detector measures is 10/12 of it. Camera resolution for the rendered head camera: enough for a 9 cm tag at 1 m.
TAG_CELLS = 12
TAG_BLACK_CELLS = 10
MARKER_DECAL_SCALE = 0.9
MAX_MARKER_ID = 63
OFFSCREEN_SIZE = (1280, 960)


def marker_geometry(size: list[float]) -> tuple[float, float]:
    """(decal side, black square side) in metres for an object of this size: the same rule for scene and perceiver."""
    decal = MARKER_DECAL_SCALE * min(size)
    return decal, decal * TAG_BLACK_CELLS / TAG_CELLS


def tag_image(marker_id: int) -> Path:
    if not isinstance(marker_id, int) or isinstance(marker_id, bool) or not 0 <= marker_id <= MAX_MARKER_ID:
        raise SceneError(f"marker_id must be an integer from 0 to {MAX_MARKER_ID}, got {marker_id!r}")
    path = MARKERS_DIR / f"tag36h11_{marker_id:02d}.png"
    if not path.is_file():
        raise SceneError(f"no tag image for marker {marker_id}: {path}")
    return path


def load_scene_yaml(path: Path | str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# Kept importable from here: the validation helpers moved to `values` so that the terrain builder can share them.
_name, _num = name, num

DEFAULT_OBSTACLE_RGB = (0.55, 0.55, 0.60)
DEFAULT_OBJECT_RGB = (0.80, 0.25, 0.25)


@dataclass
class CompiledScene:
    """The MJCF of a scene and what the adapter needs after loading it: the terrain heights to write into the height
    fields, and where each obstacle stands (on the ground under it)."""

    xml: str
    terrain: TerrainMap | None = None
    terrain_spec: TerrainSpec | None = None
    obstacle_bases: list[float] = field(default_factory=list)

    def ground_height(self, x: float, y: float) -> float:
        return self.terrain.height_at(x, y) if self.terrain is not None else 0.0

    def viewer_terrain(self) -> dict[str, Any] | None:
        return viewer_copy(self.terrain, self.obstacle_bases) if self.terrain is not None else None


Rgb = tuple[float, float, float]
Ground = Callable[[float, float], float]


class Obstacle(NamedTuple):
    """A box with collision, standing on the highest ground under it."""

    name: str
    x: float
    y: float
    base: float  # height of the ground it stands on
    w: float
    d: float
    h: float
    rgb: Rgb

    def covers(self, x: float, y: float) -> bool:
        return abs(x - self.x) <= self.w / 2 and abs(y - self.y) <= self.d / 2


class Thing(NamedTuple):
    """An object of the scene as it is drawn: a box without collision, with its tag on the side faces if it has one."""

    centre: tuple[float, float, float]
    size: tuple[float, float, float]
    marker_id: int | None
    rgb: Rgb


def compile_scene(scene: dict[str, Any], robot_file: str = "g1.xml") -> CompiledScene:
    """MJCF for a scene. Every value from the file is validated before it is written into the XML."""
    spec = parse_terrain(scene["terrain"]) if scene.get("terrain") is not None else None
    terrain = build_terrain(spec) if spec is not None else None
    if spec is not None:
        _check_geofence_inside(scene, spec)
    ground: Ground = terrain.height_at if terrain is not None else lambda x, y: 0.0
    obstacles = _obstacles(scene, terrain)

    def surface_below(x: float, y: float) -> float:
        """The ground, or the top of an obstacle under the point: where an object without a height rests."""
        return max([ground(x, y)] + [o.base + o.h for o in obstacles if o.covers(x, y)])

    objects = _objects(scene, surface_below)
    landmarks = _landmarks(scene, {o.marker_id: n for n, o in objects.items() if o.marker_id is not None})
    tags = [o.marker_id for o in objects.values() if o.marker_id is not None] + [lm.marker_id for lm, _ in landmarks]
    ter_assets, ter_geoms = terrain_mjcf(terrain, spec) if terrain is not None else ([], [])
    outdoor = terrain is not None and spec.surface != "floor"
    parts: list[str] = [
        '<mujoco model="spingi scene">',
        f'  <include file="{robot_file}"/>',
        *_visual(outdoor),
        "  <asset>",
        *_ground_assets(outdoor),
        *ter_assets,
        *_tag_assets(tags),
        "  </asset>",
        "  <worldbody>",
        _light(outdoor),
        _floor(terrain),
        *ter_geoms,
        f"    {_tracking_camera()}",
        *_location_sites(scene, ground),
        *(_obstacle_geom(o) for o in obstacles),
    ]
    for n, thing in objects.items():
        parts += _object_body(n, thing)
    for lm, post in landmarks:
        parts += _landmark_body(lm, ground(lm.pose.x, lm.pose.y), post)
    parts += ["  </worldbody>", "</mujoco>"]
    return CompiledScene(
        xml="\n".join(parts) + "\n",
        terrain=terrain,
        terrain_spec=spec,
        obstacle_bases=[o.base for o in obstacles],
    )


def _obstacles(scene: dict[str, Any], terrain: TerrainMap | None) -> list[Obstacle]:
    out = []
    for i, obs in enumerate(scene.get("obstacles") or []):
        x, y = _num(obs["x"], f"obstacle {i} x"), _num(obs["y"], f"obstacle {i} y")
        w = _num(obs.get("w", 0.5), f"obstacle {i} w", positive=True)
        d = _num(obs.get("d", 0.5), f"obstacle {i} d", positive=True)
        h = _num(obs.get("h", 1.0), f"obstacle {i} h", positive=True)
        rgb = color(obs["color"], f"obstacle {i} color") if "color" in obs else DEFAULT_OBSTACLE_RGB
        base = terrain.max_height_over((x - w / 2, x + w / 2), (y - d / 2, y + d / 2)) if terrain is not None else 0.0
        out.append(Obstacle(f"obs_{i}", x, y, base, w, d, h, rgb))
    return out


def _visual(outdoor: bool) -> list[str]:
    headlight = (
        '    <headlight diffuse="0.25 0.25 0.25" ambient="0.35 0.35 0.38" specular="0.1 0.1 0.1"/>'
        if outdoor
        else '    <headlight diffuse="0.35 0.35 0.35" ambient="0.25 0.25 0.25" specular="0.2 0.2 0.2"/>'
    )
    return [
        "  <visual>",
        headlight,
        f'    <global azimuth="140" elevation="-20" offwidth="{OFFSCREEN_SIZE[0]}" offheight="{OFFSCREEN_SIZE[1]}"/>',
        '    <quality shadowsize="4096"/>',
        '    <map haze="0.15"/>',  # clipping planes: set in metres by the adapter
        "  </visual>",
    ]


def _ground_assets(outdoor: bool) -> list[str]:
    """The sky, and the floor's texture: a field outdoors, sealed concrete in 1 m tiles indoors."""
    if outdoor:
        sky = (
            '    <texture type="skybox" builtin="gradient" rgb1="0.62 0.78 0.95" rgb2="0.95 0.97 1.0" '
            'width="256" height="1536"/>'
        )
        # Around the terrain: a field, seen in the distance. The terrain ends; the world does not.
        floor = (
            '    <texture type="2d" name="floor_tex" builtin="flat" mark="random" random="0.35" rgb1="0.40 0.44 0.30" '
            'markrgb="0.33 0.37 0.24" width="128" height="128"/>'
        )
    else:
        sky = (
            '    <texture type="skybox" builtin="gradient" rgb1="0.85 0.87 0.95" rgb2="0.55 0.6 0.8" '
            'width="256" height="1536"/>'
        )
        # Indoors: sealed concrete in 1 m tiles, the grid a person reads distances on.
        floor = (
            '    <texture type="2d" name="floor_tex" builtin="checker" mark="edge" rgb1="0.50 0.51 0.54" '
            'rgb2="0.47 0.48 0.51" markrgb="0.40 0.41 0.45" width="300" height="300"/>'
        )
    material = (
        '    <material name="floor_mat" texture="floor_tex" texuniform="true" texrepeat="1 1" '
        f'reflectance="{0 if outdoor else 0.03}"/>'
    )
    return [sky, floor, material]


def _tag_assets(tags: list[int]) -> list[str]:
    out = []
    for marker_id in tags:
        out.append(f'    <texture name="tag_{marker_id}" type="cube" file="{tag_image(marker_id)}"/>')
        out.append(f'    <material name="tag_{marker_id}" texture="tag_{marker_id}" rgba="1 1 1 1"/>')
    return out


def _light(outdoor: bool) -> str:
    if outdoor:
        return (
            '    <light name="sun" pos="0 0 40" dir="-0.35 -0.25 -1" directional="true" castshadow="true" '
            'diffuse="0.75 0.73 0.68" specular="0.2 0.2 0.2"/>'
        )
    return '    <light pos="2 2 4" dir="-0.3 -0.3 -1" directional="true" diffuse="0.55 0.55 0.55"/>'


def _floor(terrain: TerrainMap | None) -> str:
    """The floor plane. With a terrain it lies below the terrain and around it: a backstop, never above any ground the
    robot walks on."""
    friction = contact_attrs(SURFACES["floor"].friction)
    if terrain is None:
        return f'    <geom name="floor" type="plane" size="0 0 0.05" material="floor_mat" {friction}/>'
    below = float(terrain.heights.min()) - 0.05
    return f'    <geom name="floor" type="plane" size="0 0 0.05" pos="0 0 {below}" material="floor_mat" {friction}/>'


def _location_sites(scene: dict[str, Any], ground: Ground) -> list[str]:
    out = []
    for loc_name, loc in (scene.get("locations") or {}).items():
        n = _name(loc_name, "location")
        x, y = _num(loc["pose"]["x"], f"{n}.x"), _num(loc["pose"]["y"], f"{n}.y")
        z = ground(x, y) + 0.004
        out.append(
            f'    <site name="loc_{n}" type="cylinder" pos="{x} {y} {z}" size="0.25 0.004" rgba="0.42 0.30 0.95 0.55"/>'
        )
    return out


def _obstacle_geom(o: Obstacle) -> str:
    return (
        f'    <geom name="{o.name}" type="box" pos="{o.x} {o.y} {o.base + o.h / 2}" '
        f'size="{o.w / 2} {o.d / 2} {o.h / 2}" rgba="{_rgba(o.rgb)}"/>'
    )


def _object_body(n: str, thing: Thing) -> list[str]:
    """One static body per object, moved by the adapter (kinematic grasp): the box and its tag decals move together.
    Massless: the tracking camera follows the centre of mass of whatever has one, and that must be the robot."""
    (x, y, z), (sx, sy, sz) = thing.centre, thing.size
    out = [
        f'    <body name="objb_{n}" pos="{x} {y} {z}">',
        f'      <geom name="obj_{n}" type="box" size="{sx / 2} {sy / 2} {sz / 2}" rgba="{_rgba(thing.rgb)}" '
        'contype="0" conaffinity="0" density="0"/>',
    ]
    if thing.marker_id is not None:
        decal, _ = marker_geometry([sx, sy, sz])
        hd = decal / 2
        for face, pos, size in (
            ("xp", f"{sx / 2 + 0.001} 0 0", f"0.0005 {hd} {hd}"),
            ("xm", f"{-(sx / 2 + 0.001)} 0 0", f"0.0005 {hd} {hd}"),
            ("yp", f"0 {sy / 2 + 0.001} 0", f"{hd} 0.0005 {hd}"),
            ("ym", f"0 {-(sy / 2 + 0.001)} 0", f"{hd} 0.0005 {hd}"),
        ):
            out.append(
                f'      <geom name="tag_{n}_{face}" type="box" pos="{pos}" size="{size}" '
                f'material="tag_{thing.marker_id}" contype="0" conaffinity="0" density="0"/>'
            )
    out.append("    </body>")
    return out


def build_scene_xml(scene: dict[str, Any], robot_file: str = "g1.xml") -> str:
    return compile_scene(scene, robot_file).xml


def _check_geofence_inside(scene: dict[str, Any], spec: TerrainSpec) -> None:
    """A robot inside the geofence must always have ground under it."""
    fence = ((scene.get("safety") or {}).get("geofence")) or None
    if not fence:
        return
    inside = (
        spec.x_min <= fence["x_min"]
        and fence["x_max"] <= spec.x_max
        and spec.y_min <= fence["y_min"]
        and fence["y_max"] <= spec.y_max
    )
    if not inside:
        raise SceneError("terrain.extent must cover the safety geofence: the robot would walk off the ground")


def _rgba(rgb: Rgb) -> str:
    return " ".join(f"{v:.3f}" for v in rgb) + " 1"


def _landmarks(scene: dict[str, Any], object_tags: dict[int, str]) -> list[tuple[Landmark, bool]]:
    """Validated landmarks, each with whether it stands on a post. A marker id names one tag in the scene."""
    try:
        landmarks = parse_landmarks(scene)
    except (ValueError, TypeError, KeyError) as exc:  # pydantic's ValidationError is a ValueError
        raise SceneError(f"landmarks: {exc}") from exc
    out = []
    for lm, raw in zip(landmarks, scene.get("landmarks") or [], strict=True):
        tag_image(lm.marker_id)
        if lm.marker_id in object_tags:
            raise SceneError(f"marker_id {lm.marker_id} is used by both {object_tags[lm.marker_id]} and a landmark")
        post = raw.get("post", True)
        if not isinstance(post, bool):
            raise SceneError(f"landmark {lm.marker_id}: post must be true or false")
        out.append((lm, post))
    return out


def _landmark_body(lm: Landmark, ground_z: float, post: bool) -> list[str]:
    """A tag on a white plate, facing `lm.pose.yaw`, on a grey post down to the ground unless it is on a wall. No mass:
    the tracking camera follows the centre of mass of everything that has one, and that must be the robot."""
    half = lm.size_m * TAG_CELLS / TAG_BLACK_CELLS / 2  # the decal carries the white quiet zone around the tag
    yaw = lm.pose.yaw
    out = [
        f'    <body name="lmb_{lm.marker_id}" pos="{lm.pose.x} {lm.pose.y} {ground_z + lm.height_m}" '
        f'quat="{math.cos(yaw / 2)} 0 0 {math.sin(yaw / 2)}">',
        f'      <geom name="lm_{lm.marker_id}_tag" type="box" size="0.0005 {half} {half}" '
        f'material="tag_{lm.marker_id}" contype="0" conaffinity="0" density="0"/>',
        f'      <geom name="lm_{lm.marker_id}_plate" type="box" pos="-0.011 0 0" '
        f'size="0.01 {half + 0.02} {half + 0.02}" rgba="0.93 0.93 0.91 1" contype="0" conaffinity="0" density="0"/>',
    ]
    pole = lm.height_m - half - 0.02
    if post and pole > 0:
        out.append(
            f'      <geom name="lm_{lm.marker_id}_post" type="box" pos="-0.045 0 {-(half + 0.02) - pole / 2}" '
            f'size="0.025 0.025 {pole / 2}" rgba="0.42 0.44 0.47 1" contype="0" conaffinity="0" density="0"/>'
        )
    out.append("    </body>")
    return out


def _objects(scene: dict[str, Any], surface_below: Ground = lambda x, y: 0.0) -> dict[str, Thing]:
    """Validated objects, by name. Marker ids are unique: a tag names one object.
    An object without a `z` rests on what is under it (the ground or an obstacle top)."""
    out = {}
    seen: dict[int, str] = {}
    for oid, obj in (scene.get("objects") or {}).items():
        n = _name(oid, "object")
        p = obj.get("pose") or {}
        size = obj.get("size", [0.15, 0.1, 0.1])
        if not isinstance(size, list) or len(size) != 3:
            raise SceneError(f"{n}.size must be a list of three numbers")
        sx, sy, sz = (_num(v, f"{n}.size", positive=True) for v in size)
        x, y = (_num(p.get(k, 0), f"{n}.{k}") for k in ("x", "y"))
        z = _num(p["z"], f"{n}.z") if "z" in p else surface_below(x, y) + sz / 2
        marker_id = obj.get("marker_id")
        if marker_id is not None:
            tag_image(marker_id)  # validates the id and that its image exists
            if marker_id in seen:
                raise SceneError(f"marker_id {marker_id} is used by both {seen[marker_id]} and {n}")
            seen[marker_id] = n
        rgb = color(obj["color"], f"{n}.color") if "color" in obj else DEFAULT_OBJECT_RGB
        out[n] = Thing((x, y, z), (sx, sy, sz), marker_id, rgb)
    return out


def write_scene(
    scene_path: Path | str, model_dir: Path | str = DEFAULT_MODEL_DIR, robot_file: str = "g1.xml"
) -> tuple[Path, CompiledScene]:
    """Writes the MJCF next to the robot model (includes and meshes resolve from there) under a unique name, so
    concurrent runs never overwrite each other. The caller deletes it once MuJoCo has loaded it."""
    compiled = compile_scene(load_scene_yaml(scene_path), robot_file=robot_file)
    fd, path = tempfile.mkstemp(prefix="_gen_", suffix=".xml", dir=model_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(compiled.xml)
    return Path(path), compiled


def _tracking_camera() -> str:
    """Third-person camera that tracks the model's center of mass (the robot dominates the mass)."""
    offset = (-2.6, -3.2, 2.1)
    fwd = _normalize(tuple(-c for c in offset))
    right = _normalize(_cross(fwd, (0.0, 0.0, 1.0)))
    up = _cross(right, fwd)
    xy = " ".join(f"{v:.4f}" for v in right + up)
    return f'<camera name="track" mode="trackcom" pos="{offset[0]} {offset[1]} {offset[2]}" xyaxes="{xy}" fovy="55"/>'


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _normalize(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)
