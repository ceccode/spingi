"""What a real head camera does to an image (ADR-0016): a slightly soft lens, exposure that changes from frame to
frame, sensor noise that grows with brightness, and JPEG compression, which is how the Go2 streams its camera.

A rendered frame is perfect; a perceiver tested only on perfect frames is tested on the wrong thing. The degraded
image is the one the perceiver reads and the one written into the episode, as the robot would have sent it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CameraNoise:
    blur_px: float = 0.7  # optics and focus: a Gaussian blur, in pixels
    gain: float = 0.06  # sigma of the exposure from one frame to the next (auto-exposure hunting, clouds)
    read_noise: float = 2.5  # sigma, in grey levels of 255: the noise floor in the dark
    shot_noise: float = 0.04  # variance per grey level: brighter pixels are noisier (photon noise)
    jpeg_quality: int = 80
    focal_error: float = 0.01  # per run, sigma: the calibration the robot believes in is about 1 % off


def degrade(image: np.ndarray, rng: np.random.Generator, noise: CameraNoise) -> tuple[np.ndarray, bytes]:
    """The image as the camera delivers it (RGB, same size), and its JPEG bytes."""
    from PIL import Image, ImageFilter

    img = Image.fromarray(image)
    if noise.blur_px > 0:
        img = img.filter(ImageFilter.GaussianBlur(noise.blur_px))
    x = np.asarray(img, dtype=np.float32) * np.float32(1.0 + rng.normal(0.0, noise.gain))
    sigma = np.sqrt(noise.read_noise**2 + noise.shot_noise * np.clip(x, 0.0, 255.0))
    x += rng.standard_normal(x.shape, dtype=np.float32) * sigma
    noisy = Image.fromarray(np.clip(x, 0.0, 255.0).astype(np.uint8))
    buf = io.BytesIO()
    noisy.save(buf, format="JPEG", quality=noise.jpeg_quality)
    jpeg = buf.getvalue()
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB")), jpeg
