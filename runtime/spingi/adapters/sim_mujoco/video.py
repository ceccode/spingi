"""Videos written frame by frame while the simulation runs, never kept in memory: the third-person `run.mp4` and the
head camera's `head.mp4`. Frames come at a fixed rate of simulated time, so frame i was taken at
`start_t + i / fps`, which is how a reader puts the video on the episode's timeline.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

KEYFRAME_EVERY_S = 2.0  # a reader that jumps to any time decodes at most this much video


class VideoStream:
    def __init__(self, path: Path, fps: float, quality: float) -> None:
        self.path = path
        self.fps = fps
        self.quality = quality  # imageio's 0 to 10, which it maps to x264's constant rate factor
        self.frames = 0
        self.start_t: float | None = None  # simulated time of the first frame
        self.size: tuple[int, int] | None = None  # (width, height)
        self.ended = False
        self._writer = None

    def add(self, image: np.ndarray | None, t: float) -> None:
        """The frame taken at simulated time `t`. A frame that could not be rendered ends the video: frames are at a
        fixed rate, so a gap would put every later frame at the wrong time."""
        if self.ended:
            return
        if image is None:
            self.ended = True
            return
        if self._writer is None:
            import imageio.v2 as iio

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = iio.get_writer(
                self.path,
                fps=self.fps,
                codec="libx264",
                quality=self.quality,
                macro_block_size=1,
                ffmpeg_params=["-g", str(max(1, round(KEYFRAME_EVERY_S * self.fps))), "-movflags", "+faststart"],
            )
            self.start_t = t
            self.size = (image.shape[1], image.shape[0])
        self._writer.append_data(image)
        self.frames += 1

    def close(self) -> Path | None:
        """Finishes the file; its path, or None if no frame was written."""
        if self._writer is None:
            return None
        self._writer.close()
        self._writer = None
        return self.path
