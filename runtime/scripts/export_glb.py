"""Exports a robot as an articulated GLB for the viewer, from the mesh data MuJoCo already loaded.

Usage: uv run python scripts/export_glb.py [--robot g1|go2] [out.glb]

One glTF node per MuJoCo body, named after the body, with the body's transform relative to its parent at joint
angle zero; under it, the body's visual geoms (group 2) with their MJCF colours. Next to the GLB, `<alias>.joints.json`
tells the viewer which node each hinge joint turns, about which axis, and the standing angles to show when an
episode has no joint angles (kinematic runs). The root body sits at the standing height, Z up. The output goes to
viewer/public/models/ unless a path is given; `npm run model:compress` shrinks it afterwards, keeping the nodes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mujoco
import numpy as np
import trimesh

from spingi.robots import PROFILES, get_profile

VIEWER_MODELS = Path(__file__).resolve().parents[2] / "viewer" / "public" / "models"


def _matrix(pos: np.ndarray, quat: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, quat)
    m[:3, :3] = rot.reshape(3, 3)
    m[:3, 3] = pos
    return m


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--robot", choices=sorted(PROFILES), default="g1")
    parser.add_argument("out", nargs="?", type=Path)
    args = parser.parse_args()
    profile = get_profile(args.robot)
    out = args.out or VIEWER_MODELS / f"{profile.alias}.glb"

    model = mujoco.MjModel.from_xml_path(str(profile.sim.path / "scene.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    root = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, profile.sim.root_body)

    scene = trimesh.Scene()
    base = scene.graph.base_frame
    names = {b: mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b) for b in range(model.nbody)}
    for b in range(1, model.nbody):
        parent = model.body_parentid[b]
        if b != root and parent == 0:
            continue  # not part of the robot (scene.xml may add bodies)
        if b == root:
            local = _matrix(np.array([0.0, 0.0, profile.sim.base_z]), np.array([1.0, 0.0, 0.0, 0.0]))
            scene.graph.update(frame_from=base, frame_to=names[b], matrix=local)
        else:
            local = _matrix(model.body_pos[b], model.body_quat[b])
            scene.graph.update(frame_from=names[parent], frame_to=names[b], matrix=local)

    count = 0
    for g in range(model.ngeom):
        if model.geom_group[g] != 2 or model.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        mid = model.geom_dataid[g]
        va, vn = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
        fa, fn = model.mesh_faceadr[mid], model.mesh_facenum[mid]
        mesh = trimesh.Trimesh(
            vertices=model.mesh_vert[va : va + vn].astype(np.float64),
            faces=model.mesh_face[fa : fa + fn].astype(np.int64),
            process=False,
        )
        rgba = model.mat_rgba[model.geom_matid[g]] if model.geom_matid[g] >= 0 else model.geom_rgba[g]
        color = (np.clip(rgba, 0, 1) * 255).astype(np.uint8)
        mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=np.tile(color, (len(mesh.faces), 1)))
        body = names[model.geom_bodyid[g]]
        scene.add_geometry(
            mesh,
            node_name=f"{body}__geom{g}",
            geom_name=f"{body}__geom{g}",
            parent_node_name=body,
            transform=_matrix(model.geom_pos[g], model.geom_quat[g]),
        )
        count += 1

    joints = []
    for j in range(model.njnt):
        if model.jnt_type[j] != mujoco.mjtJoint.mjJNT_HINGE:
            continue
        joints.append(
            {
                "name": model.joint(j).name,
                "body": names[model.jnt_bodyid[j]],
                "axis": [round(float(v), 6) for v in model.jnt_axis[j]],
                "stand": round(float(data.qpos[model.jnt_qposadr[j]]), 4),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    scene.export(out)
    meta = {"robot": profile.name, "root": names[root], "base_z": profile.sim.base_z, "joints": joints}
    joints_path = out.with_suffix(".joints.json")
    joints_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"{profile.name}: {count} meshes on {model.nbody - 1} bodies, {len(joints)} joints -> {out}, {joints_path}")


if __name__ == "__main__":
    main()
