"""Generates the MJCF of a scene from the declarative YAML (ADR-0004).

The scene includes the G1 model and adds the floor, locations (visual discs), obstacles (boxes with
collision) and objects (boxes without collision in v0). The generated file goes into the model folder
because `<include>` and `meshdir` resolve relative to the main file.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MODEL_DIR = Path("sim/models/unitree_g1")


def load_scene_yaml(path: Path | str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_scene_xml(scene: dict[str, Any], robot_file: str = "g1.xml") -> str:
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
        p = loc["pose"]
        parts.append(
            f'    <site name="loc_{name}" type="cylinder" pos="{p["x"]} {p["y"]} 0.004" size="0.25 0.004" '
            f'rgba="0.42 0.30 0.95 0.55"/>'
        )
    for i, obs in enumerate(scene.get("obstacles") or []):
        w, d, h = obs.get("w", 0.5), obs.get("d", 0.5), obs.get("h", 1.0)
        parts.append(
            f'    <geom name="obs_{i}" type="box" pos="{obs["x"]} {obs["y"]} {h / 2}" '
            f'size="{w / 2} {d / 2} {h / 2}" rgba="0.55 0.55 0.6 1"/>'
        )
    for oid, obj in (scene.get("objects") or {}).items():
        p = obj.get("pose") or {}
        size = obj.get("size", [0.15, 0.1, 0.1])
        parts.append(
            f'    <geom name="obj_{oid}" type="box" pos="{p.get("x", 0)} {p.get("y", 0)} {p.get("z", 0.05)}" '
            f'size="{size[0] / 2} {size[1] / 2} {size[2] / 2}" rgba="0.8 0.25 0.25 1" contype="0" conaffinity="0"/>'
        )
    parts += ["  </worldbody>", "</mujoco>"]
    return "\n".join(parts) + "\n"


def write_scene(scene_path: Path | str, model_dir: Path | str = DEFAULT_MODEL_DIR) -> Path:
    scene = load_scene_yaml(scene_path)
    out = Path(model_dir) / f"_gen_{Path(scene_path).stem}.xml"
    out.write_text(build_scene_xml(scene), encoding="utf-8")
    return out


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
