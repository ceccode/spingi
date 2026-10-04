import pytest

from spingi.adapters.sim_mujoco.scene import SceneError, build_scene_xml


def scene(**over):
    base = {
        "locations": {"dock": {"pose": {"x": 0, "y": 0, "yaw": 0}}},
        "obstacles": [{"x": 1, "y": 1, "w": 0.5, "d": 0.5, "h": 1}],
        "objects": {"box_1": {"cls": "box", "pose": {"x": 2, "y": 2, "z": 0.5}}},
    }
    base.update(over)
    return base


def test_valid_scene_builds():
    xml = build_scene_xml(scene())
    assert 'name="loc_dock"' in xml and 'name="obj_box_1"' in xml and 'name="obs_0"' in xml


@pytest.mark.parametrize(
    "bad",
    [
        {"locations": {'x" /><include file="/etc/passwd': {"pose": {"x": 0, "y": 0}}}},
        {"objects": {"a b": {"cls": "box", "pose": {"x": 0, "y": 0, "z": 0}}}},
        {"objects": {"<b>": {"cls": "box"}}},
    ],
)
def test_names_that_could_inject_xml_are_refused(bad):
    with pytest.raises(SceneError, match="invalid"):
        build_scene_xml(scene(**bad))


@pytest.mark.parametrize(
    "bad",
    [
        {"obstacles": [{"x": '1" /><include file="x', "y": 0}]},
        {"obstacles": [{"x": 0, "y": 0, "w": -1}]},
        {"obstacles": [{"x": float("nan"), "y": 0}]},
        {"objects": {"b": {"cls": "box", "size": [0.1, 0.1]}}},
        {"locations": {"dock": {"pose": {"x": True, "y": 0}}}},
    ],
)
def test_non_numeric_or_out_of_range_values_are_refused(bad):
    with pytest.raises(SceneError):
        build_scene_xml(scene(**bad))
