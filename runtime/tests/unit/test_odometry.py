"""What the simulated robot believes (ADR-0016): odometry that drifts as a walking robot's does, and a head camera
that blurs, adds noise and compresses its frames."""

import math

import numpy as np
import pytest

from spingi.adapters.sim_mujoco.camera import CameraNoise, degrade
from spingi.adapters.sim_mujoco.odometry import IDENTITY, Odometry, OdometryErrors, compose, inverse


def walk(odo: Odometry, steps: int, step: tuple[float, float, float], surface: str | None = None):
    """Moves the true pose by `step` (body frame) `steps` times at 50 Hz; returns the true and believed poses."""
    true = (0.0, 0.0, 0.0)
    for _ in range(steps):
        after = compose(true, step)
        odo.update(true, after, 0.02, surface)
        true = after
    return true, odo.estimate(true)


def test_planar_transforms_compose_and_invert():
    a, b = (1.0, -2.0, 0.7), (0.3, 0.5, -2.0)
    c = compose(a, b)
    assert compose(inverse(a), c) == pytest.approx(b)
    assert compose(a, inverse(a)) == pytest.approx(IDENTITY, abs=1e-12)


def test_exact_odometry_follows_the_truth():
    odo = Odometry(None, np.random.default_rng(0))
    true, believed = walk(odo, 500, (0.01, 0.002, 0.004))
    assert believed == pytest.approx(true)


def test_odometry_drifts_with_the_distance_walked_and_is_repeatable_per_seed():
    errors = [walk(Odometry(OdometryErrors(), np.random.default_rng(s)), 1000, (0.01, 0.0, 0.0)) for s in range(40)]
    gaps = [math.hypot(t[0] - b[0], t[1] - b[1]) for t, b in errors]  # after 10 m
    assert 0.1 < float(np.median(gaps)) < 1.0  # a few per cent of the distance
    short = [walk(Odometry(OdometryErrors(), np.random.default_rng(s)), 100, (0.01, 0.0, 0.0)) for s in range(40)]
    assert np.median([math.hypot(t[0] - b[0], t[1] - b[1]) for t, b in short]) < np.median(gaps) / 3
    again = walk(Odometry(OdometryErrors(), np.random.default_rng(3)), 1000, (0.01, 0.0, 0.0))
    assert again == errors[3]


def test_turning_on_the_spot_drifts_the_heading():
    gaps = []
    for s in range(40):
        true, believed = walk(
            Odometry(OdometryErrors(), np.random.default_rng(s)), 157, (0.0, 0.0, 0.02)
        )  # half a turn
        gaps.append(abs((believed[2] - true[2] + math.pi) % (2 * math.pi) - math.pi))
    assert 0.005 < float(np.median(gaps)) < 0.1


def test_loose_ground_drifts_more_than_concrete():
    def spread(surface):
        runs = [
            walk(Odometry(OdometryErrors(), np.random.default_rng(s)), 500, (0.01, 0.0, 0.0), surface)
            for s in range(40)
        ]
        return float(np.std([b[1] - t[1] for t, b in runs]))

    assert spread("gravel") > spread("concrete")


def test_relocalizing_makes_the_robot_believe_the_given_pose():
    odo = Odometry(OdometryErrors(), np.random.default_rng(1))
    true, _ = walk(odo, 300, (0.01, 0.0, 0.003))
    odo.relocalize(true, (4.0, -1.0, 0.5))
    assert odo.estimate(true) == pytest.approx((4.0, -1.0, 0.5))


def image() -> np.ndarray:
    img = np.full((96, 128, 3), 90, dtype=np.uint8)
    img[:, 64:] = 220  # an edge, for the blur to soften
    return img


def test_a_noisy_frame_is_blurred_noisy_and_compressed_like_the_robots():
    clean = image()
    noisy, jpeg = degrade(clean, np.random.default_rng(0), CameraNoise())
    assert noisy.shape == clean.shape and noisy.dtype == np.uint8
    assert jpeg[:2] == b"\xff\xd8"  # a JPEG, as the Go2 streams its camera
    assert 1.0 < float(np.std(noisy[:, :50].astype(float))) < 10.0  # noise on the grey half, not a different image
    assert 90 < noisy[48, 64].mean() < 220  # the edge is no longer a step


def test_camera_noise_is_repeatable_per_seed():
    a, _ = degrade(image(), np.random.default_rng(5), CameraNoise())
    b, _ = degrade(image(), np.random.default_rng(5), CameraNoise())
    c, _ = degrade(image(), np.random.default_rng(6), CameraNoise())
    assert np.array_equal(a, b) and not np.array_equal(a, c)
