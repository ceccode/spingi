"""Exports the G1 in the "stand" pose as a single GLB for the viewer, from the mesh data MuJoCo already loaded.

Usage: uv run python scripts/export_g1_glb.py [out.glb]
Takes only the visual geoms (group 2), applies each geom's world pose with the base at the origin,
assigns the MJCF material color. The robot in the GLB is at (0,0,0), yaw 0, Z up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import mujoco
import numpy as np
import trimesh

MODEL = Path("sim/models/unitree_g1/scene.xml")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../viewer/public/models/g1.glb")


def main() -> None:
    model = mujoco.MjModel.from_xml_path(str(MODEL))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    data.qpos[:3] = (0.0, 0.0, 0.79)
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

    OUT.parent.mkdir(parents=True, exist_ok=True)
    scene.export(OUT)
    print(f"{count} meshes -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
