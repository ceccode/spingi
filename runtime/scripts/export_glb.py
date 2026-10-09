"""Exports a robot in its standing pose as a single GLB for the viewer, from the mesh data MuJoCo already loaded.

Usage: uv run python scripts/export_glb.py [--robot g1|go2] [out.glb]
Takes only the visual geoms (group 2), applies each geom's world pose with the base at the origin,
assigns the MJCF material color. The robot in the GLB is at (0,0,0), yaw 0, Z up. The output goes to
viewer/public/models/<alias>.glb unless a path is given; `npm run model:compress` shrinks it afterwards.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mujoco
import numpy as np
import trimesh

from spingi.robots import PROFILES, get_profile

VIEWER_MODELS = Path(__file__).resolve().parents[2] / "viewer" / "public" / "models"


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
    data.qpos[:3] = (0.0, 0.0, profile.sim.base_z)
    data.qpos[3:7] = (1.0, 0.0, 0.0, 0.0)
    mujoco.mj_forward(model, data)

    scene = trimesh.Scene()
    count = 0
    for g in range(model.ngeom):
        if model.geom_group[g] != 2 or model.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        mid = model.geom_dataid[g]
        va, vn = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
        fa, fn = model.mesh_faceadr[mid], model.mesh_facenum[mid]
        verts = model.mesh_vert[va : va + vn].astype(np.float64)
        faces = model.mesh_face[fa : fa + fn].astype(np.int64)
        tf = np.eye(4)
        tf[:3, :3] = data.geom_xmat[g].reshape(3, 3)
        tf[:3, 3] = data.geom_xpos[g]
        rgba = model.geom_rgba[g].copy()
        if model.geom_matid[g] >= 0:
            rgba = model.mat_rgba[model.geom_matid[g]]
        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        mesh.apply_transform(tf)
        color = (np.clip(rgba, 0, 1) * 255).astype(np.uint8)
        mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=np.tile(color, (len(faces), 1)))
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or f"geom_{g}"
        scene.add_geometry(mesh, node_name=name, geom_name=name)
        count += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    scene.export(out)
    print(f"{profile.name}: {count} meshes -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
