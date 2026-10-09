"""MarkerPerceiver: AprilTags (36h11) read from the image a Frame refers to (runtime-spec section 6, M4.0).

The first perceiver that works on pixels. It needs three things, all data: the image, the camera model the
adapter wrote in the frame (intrinsics and the camera's world pose), and the list of markers the site knows,
each tied to an object id and class, with the size of the tag's black square and how far the tag plane is from
the object's centre. From these it answers `detect` and `localize` in world coordinates. The same code reads
frames rendered by MuJoCo (the objects of a scene carry their tags) and, later, frames from a real camera.

Needs the `perception` extra (OpenCV). Nothing here moves the robot or touches the adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from spingi.adapters.sim_mujoco.scene import load_scene_yaml, marker_geometry
from spingi.core.ports import Frame
from spingi.core.types import ObjectRef, Pose3D

ImageSource = Callable[[Frame], "np.ndarray | None"]


class MarkerSpec(BaseModel):
    marker_id: int = Field(ge=0)
    object_id: str
    cls: str
    size_m: float = Field(gt=0, description="side of the tag's black square")
    inset_m: float = Field(ge=0, description="distance from the tag plane to the object's centre")


class Detection(BaseModel):
    marker_id: int
    object_id: str
    cls: str
    marker: Pose3D  # the tag's centre, in the world
    pose: Pose3D  # the object's centre, in the world
    pixels: float  # side of the detected square in pixels: how confident the pose can be


class MarkerPerceiver:
    def __init__(self, markers: list[MarkerSpec], images: ImageSource, min_pixels: float = 12.0) -> None:
        import cv2  # optional dependency: the `perception` extra

        self.markers = {m.marker_id: m for m in markers}
        self.images = images
        self.min_pixels = min_pixels  # a smaller square decodes, but its pose is noise
        self._dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(self._dictionary, params)
        self.last_error: str | None = None  # why the last frame gave nothing, for the log and the tests
        self.last_detections: list[Detection] = []

    # --- construction -------------------------------------------------------
    @classmethod
    def specs_from_scene(cls, scene: dict[str, Any]) -> list[MarkerSpec]:
        """One spec per object with a marker: the tag size follows the scene's rule, the inset is half the smallest
        horizontal extent (a box that is not square in plan is off by half the difference on its long faces)."""
        specs = []
        for oid, obj in (scene.get("objects") or {}).items():
            marker_id = obj.get("marker_id")
            if marker_id is None:
                continue
            size = obj.get("size", [0.15, 0.1, 0.1])
            _, black = marker_geometry(size)
            specs.append(
                MarkerSpec(marker_id=marker_id, object_id=oid, cls=obj["cls"], size_m=black, inset_m=min(size[:2]) / 2)
            )
        return specs

    @classmethod
    def for_adapter(cls, adapter: Any, scene_path: Path | str) -> MarkerPerceiver:
        """Reads the images the simulator keeps in memory for its recent frames."""
        return cls(cls.specs_from_scene(load_scene_yaml(scene_path)), images=lambda f: adapter.image(f.id))

    @classmethod
    def for_files(cls, base_dir: Path | str, scene_path: Path | str) -> MarkerPerceiver:
        """Reads the PNG a frame's `data_ref` points to, relative to `base_dir` (an episode folder, or wherever a
        real camera driver writes its frames)."""
        base = Path(base_dir)

        def read(frame: Frame) -> np.ndarray | None:
            import cv2

            if not frame.data_ref:
                return None
            path = (base / frame.data_ref).resolve()
            if base.resolve() not in path.parents:  # a frame cannot point outside its folder
                return None
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        return cls(cls.specs_from_scene(load_scene_yaml(scene_path)), images=read)

    # --- Perceiver port -----------------------------------------------------
    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]:
        return [
            ObjectRef(id=d.object_id, cls=d.cls, pose=d.pose, confidence=1.0, marker_id=d.marker_id)
            for d in self.read(frame)
            if d.cls == cls
        ]

    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None:
        for d in self.read(frame):
            if d.marker_id == marker_id:
                return d.marker
        return None

    # --- the geometry -------------------------------------------------------
    def read(self, frame: Frame) -> list[Detection]:
        """Every known marker visible in the frame, with its pose in the world."""
        import cv2

        self.last_error = None
        self.last_detections = []
        image = self.images(frame)
        if image is None:
            self.last_error = f"no image for frame {frame.id}"
            return []
        cam = frame.camera_model
        if cam is None:
            self.last_error = f"frame {frame.id} has no camera model: cannot place markers in the world"
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        K = np.array([[cam.fx, 0.0, cam.cx], [0.0, cam.fy, cam.cy], [0.0, 0.0, 1.0]])
        rot = np.array(cam.rotation).reshape(3, 3)
        pos = np.array(cam.position)
        out: list[Detection] = []
        for quad, marker_id in zip(corners, ids.ravel(), strict=True):
            spec = self.markers.get(int(marker_id))
            if spec is None:
                continue
            pts = quad[0].astype(np.float64)
            side = float(np.mean([np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)]))
            if side < self.min_pixels:
                continue
            half = spec.size_m / 2
            square = np.array(
                [[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]], dtype=np.float64
            )
            ok, rvec, tvec = cv2.solvePnP(square, pts, K, np.zeros(5), flags=cv2.SOLVEPNP_IPPE_SQUARE)
            if not ok:
                continue
            centre = pos + rot @ tvec.ravel()
            normal = rot @ cv2.Rodrigues(rvec)[0][:, 2]  # the tag's z axis points out of the face, toward the camera
            inside = centre - normal * spec.inset_m
            out.append(
                Detection(
                    marker_id=int(marker_id),
                    object_id=spec.object_id,
                    cls=spec.cls,
                    marker=Pose3D(x=float(centre[0]), y=float(centre[1]), z=float(centre[2])),
                    pose=Pose3D(x=float(inside[0]), y=float(inside[1]), z=float(inside[2])),
                    pixels=round(side, 1),
                )
            )
        self.last_detections = out
        return out
