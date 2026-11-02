"""Videos written while the simulation runs: frame i is at start_t + i / fps, so no frame may be skipped."""

import numpy as np

from spingi.adapters.sim_mujoco.video import VideoStream

IMAGE = np.zeros((48, 64, 3), np.uint8)


def test_a_frame_that_could_not_be_rendered_ends_the_video(tmp_path):
    video = VideoStream(tmp_path / "head.mp4", fps=10.0, quality=3)
    video.add(IMAGE, 0.1)
    video.add(None, 0.2)
    video.add(IMAGE, 0.3)  # after a gap it would be shown at 0.2 s: left out
    assert video.close() == tmp_path / "head.mp4"
    assert (video.frames, video.start_t, video.size) == (1, 0.1, (64, 48))
    assert (tmp_path / "head.mp4").stat().st_size > 0


def test_a_video_without_frames_writes_no_file(tmp_path):
    video = VideoStream(tmp_path / "head.mp4", fps=10.0, quality=3)
    video.add(None, 0.1)
    assert video.close() is None and not (tmp_path / "head.mp4").exists()
