"""Generates the MJCF of a scene from the declarative YAML (ADR-0004).

The scene includes the robot model (G1 by default, see `spingi.robots`) and adds the floor, locations (visual
discs), obstacles (boxes with collision) and objects (boxes without collision in v0). The generated file goes
into the model folder because `<include>` and `meshdir` resolve relative to the main file.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

# Resolved from this file, never from the working directory: a folder you run `spingi` in must not supply the robot.
MODELS_DIR = Path(__file__).resolve().parents[3] / "sim" / "models"
DEFAULT_MODEL_DIR = MODELS_DIR / "unitree_g1"


def load_scene_yaml(path: Path | str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class SceneError(ValueError):
    """The scene file contains a value that cannot go into the simulator model."""


_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _name(value: Any, what: str) -> str:
    """Names end up in XML attributes: only letters, digits, `_` and `-` (no quotes, angle brackets, spaces)."""
    if not isinstance(value, str) or not _NAME.match(value):
        raise SceneError(f"invalid {what} name {value!r}: use letters, digits, '_' or '-', at most 64 characters")
    return value


def _num(value: Any, what: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SceneError(f"{what} must be a finite number, got {value!r}")
    if positive and value <= 0:
        raise SceneError(f"{what} must be > 0, got {value!r}")
    return float(value)


def build_scene_xml(scene: dict[str, Any], robot_file: str = "g1.xml") -> str:
    """MJCF for a scene. Every value from the file is validated before it is written into the XML."""
    parts: list[str] = [
        '<mujoco model="spingi scene">',
        f'  <include file="{robot_file}"/>',
        "  <visual>",
        '    <headlight diffuse="0.35 0.35 0.35" ambient="0.25 0.25 0.25" specular="0.2 0.2 0.2"/>',
        '    <global azimuth="140" elevation="-20" offwidth="640" offheight="480"/>',
        "  </visual>",
        "  <asset>",
        '    <texture type="skybox" builtin="gradient" rgb1="0.85 0.87 0.95" rgb2="0.55 0.6 0.8" '
        'width="256" height="1536"/>',
        '    <texture type="2d" name="floor_tex" builtin="checker" mark="edge" rgb1="0.52 0.52 0.58" '
        'rgb2="0.40 0.40 0.46" markrgb="0.7 0.7 0.75" width="300" height="300"/>',
        '    <material name="floor_mat" texture="floor_tex" texuniform="true" texrepeat="6 6" reflectance="0.0"/>',
        "  </asset>",
        "  <worldbody>",
        '    <light pos="2 2 4" dir="-0.3 -0.3 -1" directional="true" diffuse="0.55 0.55 0.55"/>',
        '    <geom name="floor" type="plane" size="0 0 0.05" material="floor_mat"/>',
        f"    {_tracking_camera()}",
    ]
    for name, loc in (scene.get("locations") or {}).items():
        n = _name(name, "location")
        x, y = _num(loc["pose"]["x"], f"{n}.x"), _num(loc["pose"]["y"], f"{n}.y")
        parts.append(
            f'    <site name="loc_{n}" type="cylinder" pos="{x} {y} 0.004" size="0.25 0.004" '
            f'rgba="0.42 0.30 0.95 0.55"/>'
        )
    for i, obs in enumerate(scene.get("obstacles") or []):
        x, y = _num(obs["x"], f"obstacle {i} x"), _num(obs["y"], f"obstacle {i} y")
        w = _num(obs.get("w", 0.5), f"obstacle {i} w", positive=True)
        d = _num(obs.get("d", 0.5), f"obstacle {i} d", positive=True)
        h = _num(obs.get("h", 1.0), f"obstacle {i} h", positive=True)
        parts.append(
            f'    <geom name="obs_{i}" type="box" pos="{x} {y} {h / 2}" '
            f'size="{w / 2} {d / 2} {h / 2}" rgba="0.55 0.55 0.6 1"/>'
        )
    for oid, obj in (scene.get("objects") or {}).items():
        n = _name(oid, "object")
        p = obj.get("pose") or {}
        x, y, z = (_num(p.get(k, default), f"{n}.{k}") for k, default in (("x", 0), ("y", 0), ("z", 0.05)))
        size = obj.get("size", [0.15, 0.1, 0.1])
        if not isinstance(size, list) or len(size) != 3:
            raise SceneError(f"{n}.size must be a list of three numbers")
        sx, sy, sz = (_num(v, f"{n}.size", positive=True) for v in size)
        parts.append(
            f'    <geom name="obj_{n}" type="box" pos="{x} {y} {z}" '
            f'size="{sx / 2} {sy / 2} {sz / 2}" rgba="0.8 0.25 0.25 1" contype="0" conaffinity="0"/>'
        )
    parts += ["  </worldbody>", "</mujoco>"]
    return "\n".join(parts) + "\n"


def write_scene(scene_path: Path | str, model_dir: Path | str = DEFAULT_MODEL_DIR, robot_file: str = "g1.xml") -> Path:
    """Writes the MJCF next to the robot model (includes and meshes resolve from there) under a unique name, so
    concurrent runs never overwrite each other. The caller deletes it once MuJoCo has loaded it."""
    xml = build_scene_xml(load_scene_yaml(scene_path), robot_file=robot_file)
    fd, name = tempfile.mkstemp(prefix="_gen_", suffix=".xml", dir=model_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return Path(name)


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
