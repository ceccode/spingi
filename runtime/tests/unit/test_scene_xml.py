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


def test_objects_with_a_marker_carry_four_tag_decals_in_their_own_body():
    scene = {"objects": {"red_box_01": {"cls": "red_box", "marker_id": 7, "pose": {"x": 1, "y": 2, "z": 0.9}}}}
    xml = build_scene_xml(scene)
    assert xml.count('material="tag_7"') == 4 and 'name="tag_7" type="cube"' in xml and "tag36h11_07.png" in xml
    assert '<body name="objb_red_box_01" pos="1.0 2.0 0.9">' in xml
    plain = build_scene_xml({"objects": {"crate": {"cls": "crate"}}})
    assert "tag_" not in plain and '<body name="objb_crate"' in plain


def test_marker_ids_must_be_unique_and_have_a_tag_image():
    import pytest

    from spingi.adapters.sim_mujoco.scene import SceneError

    with pytest.raises(SceneError, match="used by both"):
        build_scene_xml({"objects": {"a": {"cls": "x", "marker_id": 3}, "b": {"cls": "x", "marker_id": 3}}})
    with pytest.raises(SceneError, match="0 to 63"):
        build_scene_xml({"objects": {"a": {"cls": "x", "marker_id": 99}}})
    with pytest.raises(SceneError, match="0 to 63"):
        build_scene_xml({"objects": {"a": {"cls": "x", "marker_id": True}}})
