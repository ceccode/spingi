"""Generates the MJCF of a scene from the declarative YAML (ADR-0004).

The scene includes the robot model (G1 by default, see `spingi.robots`) and adds the floor or, with a `terrain`
section, the ground (`terrain.py`, ADR-0015), locations (visual discs), obstacles (boxes with collision, standing
on the ground) and objects (boxes without collision in v0). An object with a `marker_id` carries its AprilTag on
its four side faces, so the frames the head camera renders can be read by `MarkerPerceiver` like a real camera's
(M4.0). The generated file goes into the model folder because `<include>` and `meshdir` resolve relative to the
main file.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


def compile_scene(scene: dict[str, Any], robot_file: str = "g1.xml") -> CompiledScene:
    """MJCF for a scene. Every value from the file is validated before it is written into the XML."""
    spec = parse_terrain(scene["terrain"]) if scene.get("terrain") is not None else None
    terrain = build_terrain(spec) if spec is not None else None
    if spec is not None:
        _check_geofence_inside(scene, spec)
    ground = (lambda x, y: terrain.height_at(x, y)) if terrain is not None else (lambda x, y: 0.0)

    obstacles = []  # (name, centre x, y, base z, w, d, h, rgb)
    for i, obs in enumerate(scene.get("obstacles") or []):
        x, y = _num(obs["x"], f"obstacle {i} x"), _num(obs["y"], f"obstacle {i} y")
        w = _num(obs.get("w", 0.5), f"obstacle {i} w", positive=True)
        d = _num(obs.get("d", 0.5), f"obstacle {i} d", positive=True)
        h = _num(obs.get("h", 1.0), f"obstacle {i} h", positive=True)
        rgb = color(obs["color"], f"obstacle {i} color") if "color" in obs else DEFAULT_OBSTACLE_RGB
        base = terrain.max_height_over((x - w / 2, x + w / 2), (y - d / 2, y + d / 2)) if terrain is not None else 0.0
        obstacles.append((f"obs_{i}", x, y, base, w, d, h, rgb))

    def surface_below(x: float, y: float) -> float:
        """The ground, or the top of an obstacle under the point: where an object without a height rests."""
        top = ground(x, y)
        for _, ox, oy, base, w, d, h, _ in obstacles:
            if abs(x - ox) <= w / 2 and abs(y - oy) <= d / 2:
                top = max(top, base + h)
        return top

    objects = _objects(scene, surface_below)
    assets = []
    for _, _, marker_id, _ in objects.values():
        if marker_id is not None:
            assets.append(f'    <texture name="tag_{marker_id}" type="cube" file="{tag_image(marker_id)}"/>')
            assets.append(f'    <material name="tag_{marker_id}" texture="tag_{marker_id}" rgba="1 1 1 1"/>')
    ter_assets, ter_geoms = terrain_mjcf(terrain, spec) if terrain is not None else ([], [])
    outdoor = terrain is not None and spec.surface != "floor"
    parts: list[str] = [
        '<mujoco model="spingi scene">',
        f'  <include file="{robot_file}"/>',
        "  <visual>",
        (
            '    <headlight diffuse="0.25 0.25 0.25" ambient="0.35 0.35 0.38" specular="0.1 0.1 0.1"/>'
            if outdoor
            else '    <headlight diffuse="0.35 0.35 0.35" ambient="0.25 0.25 0.25" specular="0.2 0.2 0.2"/>'
        ),
        f'    <global azimuth="140" elevation="-20" offwidth="{OFFSCREEN_SIZE[0]}" offheight="{OFFSCREEN_SIZE[1]}"/>',
        '    <quality shadowsize="4096"/>',
        '    <map haze="0.15" zfar="200"/>',
        "  </visual>",
        "  <asset>",
        (
            '    <texture type="skybox" builtin="gradient" rgb1="0.62 0.78 0.95" rgb2="0.95 0.97 1.0" '
            'width="256" height="1536"/>'
            if outdoor
            else '    <texture type="skybox" builtin="gradient" rgb1="0.85 0.87 0.95" rgb2="0.55 0.6 0.8" '
            'width="256" height="1536"/>'
        ),
        (
            # Around the terrain: a field, seen in the distance. The terrain ends; the world does not.
            '    <texture type="2d" name="floor_tex" builtin="flat" mark="random" random="0.35" rgb1="0.40 0.44 0.30" '
            'markrgb="0.33 0.37 0.24" width="128" height="128"/>'
            if outdoor
            # Indoors: sealed concrete in 1 m tiles, the grid a person reads distances on.
            else '    <texture type="2d" name="floor_tex" builtin="checker" mark="edge" rgb1="0.50 0.51 0.54" '
            'rgb2="0.47 0.48 0.51" markrgb="0.40 0.41 0.45" width="300" height="300"/>'
        ),
        '    <material name="floor_mat" texture="floor_tex" texuniform="true" texrepeat="1 1" '
        f'reflectance="{0 if outdoor else 0.03}"/>',
        *ter_assets,
        *assets,
        "  </asset>",
        "  <worldbody>",
        (
            '    <light name="sun" pos="0 0 40" dir="-0.35 -0.25 -1" directional="true" castshadow="true" '
            'diffuse="0.75 0.73 0.68" specular="0.2 0.2 0.2"/>'
            if outdoor
            else '    <light pos="2 2 4" dir="-0.3 -0.3 -1" directional="true" diffuse="0.55 0.55 0.55"/>'
        ),
    ]
    if terrain is not None:
        # Below the terrain and around it: a backstop, never above any ground the robot walks on.
        below = float(terrain.heights.min()) - 0.05
        parts.append(
            f'    <geom name="floor" type="plane" size="0 0 0.05" pos="0 0 {below}" material="floor_mat" '
            f"{contact_attrs(SURFACES['floor'].friction)}/>"
        )
        parts += ter_geoms
    else:
        parts.append(
            '    <geom name="floor" type="plane" size="0 0 0.05" material="floor_mat" '
            f"{contact_attrs(SURFACES['floor'].friction)}/>"
        )
    parts.append(f"    {_tracking_camera()}")
    for loc_name, loc in (scene.get("locations") or {}).items():
        n = _name(loc_name, "location")
        x, y = _num(loc["pose"]["x"], f"{n}.x"), _num(loc["pose"]["y"], f"{n}.y")
        z = ground(x, y) + 0.004
        parts.append(
            f'    <site name="loc_{n}" type="cylinder" pos="{x} {y} {z}" size="0.25 0.004" rgba="0.42 0.30 0.95 0.55"/>'
        )
    for oname, x, y, base, w, d, h, rgb in obstacles:
        parts.append(
            f'    <geom name="{oname}" type="box" pos="{x} {y} {base + h / 2}" '
            f'size="{w / 2} {d / 2} {h / 2}" rgba="{_rgba(rgb)}"/>'
        )
    for n, ((x, y, z), (sx, sy, sz), marker_id, rgb) in objects.items():
        # One static body per object, moved by the adapter (kinematic grasp): the box and its tag decals move together.
        parts.append(f'    <body name="objb_{n}" pos="{x} {y} {z}">')
        parts.append(
            f'      <geom name="obj_{n}" type="box" size="{sx / 2} {sy / 2} {sz / 2}" rgba="{_rgba(rgb)}" '
            'contype="0" conaffinity="0"/>'
        )
        if marker_id is not None:
            decal, _ = marker_geometry([sx, sy, sz])
            hd = decal / 2
            for face, pos, size in (
                ("xp", f"{sx / 2 + 0.001} 0 0", f"0.0005 {hd} {hd}"),
                ("xm", f"{-(sx / 2 + 0.001)} 0 0", f"0.0005 {hd} {hd}"),
                ("yp", f"0 {sy / 2 + 0.001} 0", f"{hd} 0.0005 {hd}"),
                ("ym", f"0 {-(sy / 2 + 0.001)} 0", f"{hd} 0.0005 {hd}"),
            ):
                parts.append(
                    f'      <geom name="tag_{n}_{face}" type="box" pos="{pos}" size="{size}" '
                    f'material="tag_{marker_id}" contype="0" conaffinity="0"/>'
                )
        parts.append("    </body>")
    parts += ["  </worldbody>", "</mujoco>"]
    return CompiledScene(
        xml="\n".join(parts) + "\n",
        terrain=terrain,
        terrain_spec=spec,
        obstacle_bases=[base for _, _, _, base, *_ in obstacles],
    )


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


def _rgba(rgb: tuple[float, float, float]) -> str:
    return " ".join(f"{v:.3f}" for v in rgb) + " 1"


def _objects(
    scene: dict[str, Any], surface_below=lambda x, y: 0.0
) -> dict[str, tuple[tuple[float, float, float], tuple[float, float, float], int | None, tuple[float, float, float]]]:
    """Validated objects: name -> (centre, size, marker id, colour). Marker ids are unique: a tag names one object.
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
        out[n] = ((x, y, z), (sx, sy, sz), marker_id, rgb)
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
