"""MarkerPerceiver geometry on synthetic images: a tag drawn where a known camera would see it must come back at its
world pose. No simulator, no robot: the perceiver is pixels plus a camera model."""

from pathlib import Path

import numpy as np
import pytest

from spingi.adapters.sim_mujoco.scene import marker_geometry
from spingi.core.ports import CameraModel, Frame
from spingi.perception.markers import MarkerPerceiver, MarkerSpec

cv2 = pytest.importorskip("cv2")

W, H = 1280, 960
F = 700.0
# A camera 1 m above the floor at the origin, looking along +x: OpenCV z forward = world x, x right = world -y,
# y down = world -z. Columns of world_from_camera are the camera axes in the world.
CAMERA = CameraModel(
    fx=F, fy=F, cx=W / 2, cy=H / 2, width=W, height=H, position=(0.0, 0.0, 1.0), rotation=(0, 0, 1, -1, 0, 0, 0, -1, 0)
)
SPEC = MarkerSpec(marker_id=7, object_id="red_box_01", cls="red_box", size_m=0.075, inset_m=0.05)


def draw_tag(marker_id: int, centre: tuple[float, float, float], size_m: float, cam: CameraModel) -> np.ndarray:
    """A white canvas with the tag's black square drawn where `cam` projects it, facing the camera (normal = -x)."""
    rot = np.array(cam.rotation).reshape(3, 3)
    pos = np.array(cam.position)
    half = size_m / 2
    # Tag axes in the world: x along world -y (the camera's right), y up (world z), z toward the camera.
    corners_world = [
        np.array(centre) + np.array([0, half, half]),  # top-left as the camera sees it
        np.array(centre) + np.array([0, -half, half]),
        np.array(centre) + np.array([0, -half, -half]),
        np.array(centre) + np.array([0, half, -half]),
    ]
    K = np.array([[cam.fx, 0, cam.cx], [0, cam.fy, cam.cy], [0, 0, 1]])
    pixels = []
    for c in corners_world:
        p_cam = rot.T @ (c - pos)
        uv = K @ p_cam
        pixels.append(uv[:2] / uv[2])
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    tag = cv2.aruco.generateImageMarker(dictionary, marker_id, 200)
    tag = cv2.copyMakeBorder(tag, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)  # quiet zone
    src = np.array([[20, 20], [220, 20], [220, 220], [20, 220]], dtype=np.float32)  # the black square in the tag image
    hom = cv2.getPerspectiveTransform(src, np.array(pixels, dtype=np.float32))
    canvas = np.full((cam.height, cam.width), 255, dtype=np.uint8)
    warped = cv2.warpPerspective(tag, hom, (cam.width, cam.height), borderValue=255)
    return cv2.cvtColor(np.minimum(canvas, warped), cv2.COLOR_GRAY2RGB)


def frame_with(cam: CameraModel | None) -> Frame:
    return Frame(id="f1", ts=0.0, width=W, height=H, camera_model=cam)


async def test_a_tag_comes_back_at_its_world_pose_and_the_object_behind_it():
    centre = (1.0, 0.2, 0.9)  # a 7.5 cm tag one metre away: about 50 pixels per side
    image = draw_tag(7, centre, SPEC.size_m, CAMERA)
    perceiver = MarkerPerceiver([SPEC], images=lambda f: image)
    found = await perceiver.detect(frame_with(CAMERA), "red_box")
    assert [o.id for o in found] == ["red_box_01"] and found[0].marker_id == 7
    marker = await perceiver.localize(frame_with(CAMERA), 7)
    # depth (x) is the weak axis of a single tag: a fraction of a pixel on a corner is about a centimetre
    assert marker is not None and np.allclose((marker.x, marker.y, marker.z), centre, atol=0.015)
    # the object's centre is behind the tag plane by the inset, away from the camera (+x here)
    assert np.allclose((found[0].pose.x, found[0].pose.y, found[0].pose.z), (1.05, 0.2, 0.9), atol=0.015)
    assert perceiver.last_detections[0].pixels > 40


async def test_classes_and_unknown_markers_are_filtered():
    image = draw_tag(7, (1.5, 0.0, 0.9), SPEC.size_m, CAMERA)
    perceiver = MarkerPerceiver([SPEC], images=lambda f: image)
    assert await perceiver.detect(frame_with(CAMERA), "blue_crate") == []
    other = MarkerPerceiver([SPEC.model_copy(update={"marker_id": 9})], images=lambda f: image)
    assert (
        await other.detect(frame_with(CAMERA), "red_box") == [] and await other.localize(frame_with(CAMERA), 7) is None
    )


async def test_without_an_image_or_a_camera_model_nothing_is_seen_and_the_reason_is_kept():
    image = draw_tag(7, (1.5, 0.0, 0.9), SPEC.size_m, CAMERA)
    blind = MarkerPerceiver([SPEC], images=lambda f: None)
    assert await blind.detect(frame_with(CAMERA), "red_box") == [] and "no image" in blind.last_error
    no_model = MarkerPerceiver([SPEC], images=lambda f: image)
    assert await no_model.detect(frame_with(None), "red_box") == [] and "camera model" in no_model.last_error


def test_specs_follow_the_scene_rule_for_tag_size():
    scene = {"objects": {"red_box_01": {"cls": "red_box", "marker_id": 7}, "plain": {"cls": "crate"}}}
    specs = MarkerPerceiver.specs_from_scene(scene)
    assert [s.object_id for s in specs] == ["red_box_01"]
    assert specs[0].size_m == pytest.approx(marker_geometry([0.15, 0.1, 0.1])[1]) == pytest.approx(0.075)
    assert specs[0].inset_m == pytest.approx(0.05)


async def test_frames_cannot_point_outside_their_folder(tmp_path):
    (tmp_path / "scene.yaml").write_text("objects:\n  red_box_01: { cls: red_box, marker_id: 7 }\n")
    perceiver = MarkerPerceiver.for_files(tmp_path, tmp_path / "scene.yaml")
    outside = Frame(id="f", ts=0.0, data_ref="../../etc/passwd", camera_model=CAMERA)
    assert await perceiver.detect(outside, "red_box") == [] and "no image" in perceiver.last_error
    image = draw_tag(7, (1.5, 0.0, 0.9), SPEC.size_m, CAMERA)
    (tmp_path / "frames").mkdir()
    cv2.imwrite(str(tmp_path / "frames" / "f.png"), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    inside = Frame(id="f", ts=0.0, data_ref="frames/f.png", camera_model=CAMERA)
    assert [o.id for o in await perceiver.detect(inside, "red_box")] == ["red_box_01"]
    assert Path(tmp_path / "frames" / "f.png").exists()
