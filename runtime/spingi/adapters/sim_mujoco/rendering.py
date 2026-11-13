"""What the simulator draws: offscreen renders, the frames the head camera takes, and the videos of a run."""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from spingi.adapters.sim_mujoco.camera import CameraNoise, degrade
from spingi.adapters.sim_mujoco.video import VideoStream
from spingi.core.ports import CameraModel, Frame


class Renderers:
    """Images of the scene from a named camera, with one MuJoCo renderer per image size."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData) -> None:
        self._model = model
        self._data = data
        self._by_size: dict[tuple[int, int], mujoco.Renderer] = {}  # (height, width)
        self.error: str | None = None  # why the last render failed, kept for diagnosis

    def render(self, camera: str, height: int, width: int) -> np.ndarray | None:
        try:
            renderer = self._by_size.get((height, width))
            if renderer is None:
                renderer = self._by_size[(height, width)] = mujoco.Renderer(self._model, height, width)
            renderer.update_scene(self._data, camera=camera)
            return renderer.render().copy()
        except Exception as exc:  # noqa: BLE001 - no OpenGL context (headless CI): perception gets no image
            self.error = repr(exc)  # kept for diagnosis instead of being swallowed
            return None

    def close(self) -> None:
        for renderer in self._by_size.values():
            renderer.close()
        self._by_size.clear()


class HeadCamera:
    """The frames the runtime takes (`get_camera`): rendered, degraded when the camera is noisy, kept for the
    perceiver and written to the episode. Each frame says where the robot believes the camera was."""

    IMAGE_CACHE = 4  # rendered images kept in memory for the perceiver, by frame id

    def __init__(
        self,
        renderers: Renderers,
        model: mujoco.MjModel,
        data: mujoco.MjData,
        size: tuple[int, int],
        noisy: bool,
        seed: int,
        record_dir: Path | None,
    ) -> None:
        self._renderers = renderers
        self._model = model
        self._data = data
        self.size = size  # (width, height) of the frames
        self.noise = CameraNoise() if noisy else None
        self._rng = np.random.default_rng([seed, 2])  # its own stream: frames taken do not change the drift
        # The robot's belief about its camera's focal length is slightly off when the camera is noisy.
        self._focal_scale = 1.0 + (self._rng.normal(0.0, self.noise.focal_error) if noisy else 0.0)
        self._record_dir = record_dir
        self._images: dict[str, np.ndarray] = {}
        self._taken = 0

    def take(self, name: str, ts: float, offset: tuple[float, float, float]) -> Frame:
        """A frame from the camera `name` at robot time `ts`; `offset` is the odometry's, from the true frame to the
        one the robot believes in."""
        self._taken += 1
        width, height = self.size
        frame = Frame(
            id=f"sim-{self._taken}",
            ts=ts,
            camera=name,
            width=width,
            height=height,
            camera_model=self._camera_model(name, width, height, offset),
        )
        img = self._renderers.render(name, height, width)
        if img is not None:
            jpeg = None
            if self.noise is not None:
                img, jpeg = degrade(img, self._rng, self.noise)
            self._images[frame.id] = img
            while len(self._images) > self.IMAGE_CACHE:
                del self._images[next(iter(self._images))]
            if self._record_dir is not None:
                # Relative to the episode folder, no local paths in episodes. A noisy camera writes the JPEG it sent,
                # as the robot streams it; a clean render stays a lossless PNG.
                frame.data_ref = f"frames/{frame.id}.{'jpg' if jpeg is not None else 'png'}"
                out = self._record_dir / frame.data_ref
                out.parent.mkdir(parents=True, exist_ok=True)
                if jpeg is not None:
                    out.write_bytes(jpeg)
                else:
                    import imageio.v3 as iio

                    iio.imwrite(out, img)
        return frame

    def image(self, frame_id: str) -> np.ndarray | None:
        """The rendered image of a recent frame (RGB, height x width x 3), for a perceiver that reads pixels."""
        return self._images.get(frame_id)

    def _camera_model(
        self, name: str, width: int, height: int, offset: tuple[float, float, float]
    ) -> CameraModel | None:
        """The camera as the robot believes it is: placed by the pose estimate (the IMU gives the true tilt), with
        the focal length of its calibration."""
        cam = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if cam < 0:
            return None
        fovy = math.radians(float(self._model.cam_fovy[cam]))
        f = (height / 2) / math.tan(fovy / 2) * self._focal_scale  # square pixels, vertical field of view
        ox, oy, oyaw = offset
        c, s = math.cos(oyaw), math.sin(oyaw)
        turn = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        # MuJoCo camera axes: x right, y up, looking along -z. OpenCV: x right, y down, looking along +z.
        rot = turn @ self._data.cam_xmat[cam].reshape(3, 3) @ np.diag([1.0, -1.0, -1.0])
        pos = turn @ self._data.cam_xpos[cam] + np.array([ox, oy, 0.0])
        return CameraModel(
            fx=f,
            fy=f,
            cx=width / 2,
            cy=height / 2,
            width=width,
            height=height,
            position=tuple(float(v) for v in pos),
            rotation=tuple(float(v) for v in rot.ravel()),
        )


class Videos:
    """The videos of a run, for people: third person (`run.mp4`) and what the head camera sees, continuously
    (`head.mp4`). The head camera's is the clean render, from where the camera really is; the runtime takes its own
    frames (`HeadCamera`), which carry the camera noise."""

    SIZE = (640, 480)
    QUALITY, HEAD_QUALITY = 7, 3  # imageio's 0 to 10: x264 rate factors 15 and 35

    def __init__(
        self, renderers: Renderers, record_dir: Path | None, fps: float, third_person: bool, head: bool
    ) -> None:
        self._renderers = renderers
        recording = record_dir is not None
        self.third_person = (
            VideoStream(record_dir / "run.mp4", fps, self.QUALITY) if third_person and recording else None
        )
        self.head = VideoStream(record_dir / "head.mp4", fps, self.HEAD_QUALITY) if head and recording else None

    def add(self, ts: float) -> None:
        """One more frame of each video, at robot time `ts`."""
        width, height = self.SIZE
        if self.third_person is not None:
            self.third_person.add(self._renderers.render("track", height, width), ts)
        if self.head is not None:
            self.head.add(self._renderers.render("head", height, width), ts)

    def close(self) -> Path | None:
        """Finishes the videos; returns the third-person video's path, if any."""
        path = self.third_person.close() if self.third_person is not None else None
        if self.head is not None:
            self.head.close()
        return path

    def head_for_manifest(self) -> dict | None:
        """The head-camera video as the episode's manifest describes it, or None if none was written."""
        video = self.head
        if video is None or video.frames == 0 or video.size is None:
            return None
        return {
            "file": video.path.name,
            "camera": "head",
            "fps": round(video.fps, 3),
            "start_t": round(video.start_t, 3),
            "frames": video.frames,
            "width": video.size[0],
            "height": video.size[1],
        }
