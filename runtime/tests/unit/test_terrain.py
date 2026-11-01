"""Terrain from the scene file (ADR-0015): validated, the same for the same seed, seamless, and cut into height fields
that cover the ground exactly once."""

import base64

import numpy as np
import pytest

from spingi.adapters.sim_mujoco.scene import SceneError, compile_scene, load_scene_yaml
from spingi.adapters.sim_mujoco.terrain import (
    SURFACES,
    build_terrain,
    parse_terrain,
    patches,
    structures,
    viewer_copy,
)


def spec(**over):
    raw = {
        "extent": {"x_min": -2, "x_max": 10, "y_min": -3, "y_max": 3},
        "cell_m": 0.1,
        "seed": 4,
        "surface": "asphalt",
        "regions": [{"surface": "gravel", "x": [3, 6], "y": [-3, 3]}, {"surface": "grass", "x": [6, 10], "y": [0, 3]}],
        "ramps": [{"x": [1, 2], "y": [-1, 1], "from_m": 0.0, "to_m": 0.2, "along": "x"}],
        "raised": [{"x": [8, 10], "y": [-3, -1], "height_m": 0.12}],
    }
    raw.update(over)
    return raw


def test_the_same_seed_gives_the_same_ground_and_another_seed_another():
    a = build_terrain(parse_terrain(spec()))
    b = build_terrain(parse_terrain(spec()))
    c = build_terrain(parse_terrain(spec(seed=5)))
    assert np.array_equal(a.heights, b.heights) and not np.array_equal(a.heights, c.heights)


def test_ramps_and_raised_areas_have_their_heights_and_surfaces_their_roughness():
    t = build_terrain(parse_terrain(spec()))
    assert t.height_at(1.5, 0.0) == pytest.approx(0.1, abs=0.001)  # half way up a 20 cm ramp: built, smooth
    assert t.height_at(9.0, -2.0) == pytest.approx(0.12, abs=0.001)
    assert float(np.max(t.ground)) < 0.05  # the height fields carry the roughness only, not the structures
    assert t.surface_at(4.0, 0.0) == "gravel" and t.surface_at(7.0, 1.0) == "grass"
    assert t.surface_at(7.0, -1.0) == "asphalt"
    flat = build_terrain(parse_terrain(spec(regions=[], ramps=[], raised=[], surface="floor")))
    assert np.allclose(flat.heights, 0.0)  # the indoor floor has no roughness at all
    gravel = t.heights[:, int((4.5 - t.x0) / t.cell_m)]
    assert 0.003 < float(np.std(gravel)) < 0.02  # about the surface's roughness, not the asphalt's


def test_no_step_of_height_at_the_border_between_two_surfaces():
    t = build_terrain(parse_terrain(spec(ramps=[], raised=[])))
    col = round((3.0 - t.x0) / t.cell_m)  # asphalt to gravel
    jump = np.abs(t.heights[:, col + 1] - t.heights[:, col - 1])
    assert float(jump.max()) < 0.02  # roughness fades over a quarter metre instead of jumping


def test_patches_cover_the_grid_once_with_one_surface_each():
    s = parse_terrain(spec())
    t = build_terrain(s)
    seen = np.zeros_like(t.heights, dtype=int)
    for p in patches(t, s):
        block = t.labels[p.r0 : p.r1 + 1, p.c0 : p.c1 + 1]
        inner = block[1:-1, 1:-1] if block.shape[0] > 2 and block.shape[1] > 2 else block
        assert (inner == t.surfaces.index(p.surface)).all(), p
        seen[p.r0 : p.r1, p.c0 : p.c1] += 1  # each cell (not its shared border samples) belongs to one patch
    assert (seen[:-1, :-1] == 1).all()


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"extent": None}, "extent is required"),
        ({"cell_m": 0.02}, "between 0.08 and 0.25"),
        ({"surface": "lava"}, "must be one of"),
        ({"regions": [{"surface": "gravel", "x": [5, 3], "y": [0, 1]}]}, "smaller to the larger"),
        ({"ramps": [{"x": [0, 1], "y": [0, 1], "to_m": 0.2, "along": "z"}]}, "along must be"),
        ({"raised": [{"x": [0, 1], "y": [0, 1], "height_m": 50}]}, "between 0 and 3"),
        ({"raised": [{"x": [0, 1], "y": [0, 1], "height_m": -0.5}]}, "between 0 and 3"),
        ({"ramps": [{"x": [0, 0.2], "y": [0, 1], "to_m": 0.5}]}, "steeper than 45"),
        ({"regions": [{"surface": "dirt", "x": [0, 1], "y": [0, 1], "roughness_m": 0.5}]}, "roughness_m"),
        ({"seed": -1}, "seed"),
        ({"extent": {"x_min": 0, "x_max": 500, "y_min": 0, "y_max": 500}, "cell_m": 0.08}, "larger than"),
    ],
)
def test_bad_terrain_values_are_refused(over, message):
    with pytest.raises(SceneError, match=message):
        parse_terrain(spec(**over))


def test_the_terrain_must_cover_the_geofence():
    scene = {"terrain": spec(), "safety": {"geofence": {"x_min": -5, "x_max": 5, "y_min": -1, "y_max": 1}}}
    with pytest.raises(SceneError, match="cover the safety geofence"):
        compile_scene(scene)


def test_obstacles_stand_on_the_ground_and_the_mjcf_has_one_height_field_per_patch():
    scene = {"terrain": spec(), "obstacles": [{"x": 9.0, "y": -2.0, "w": 0.5, "d": 0.5, "h": 1.0, "color": "#336699"}]}
    compiled = compile_scene(scene)
    assert compiled.obstacle_bases[0] == pytest.approx(0.12, abs=0.03)  # on the raised area
    xml = compiled.xml
    assert xml.count('type="hfield"') == len(patches(compiled.terrain, compiled.terrain_spec))
    assert 'rgba="0.200 0.400 0.600 1"' in xml and 'solmix="10000"' in xml
    assert f'friction="{SURFACES["gravel"].friction} ' in xml


def test_the_viewer_copy_decodes_to_the_same_ground():
    t = build_terrain(parse_terrain(spec()))
    copy = viewer_copy(t, [0.5])
    heights = np.frombuffer(base64.b64decode(copy["heights_mm"]), dtype="<i2").reshape(copy["rows"], copy["cols"])
    ids = np.frombuffer(base64.b64decode(copy["surface_ids"]), dtype=np.uint8).reshape(copy["rows"], copy["cols"])
    stride = round(copy["cell_m"] / t.cell_m)
    assert np.allclose(heights / 1000, t.heights[::stride, ::stride], atol=0.001)
    assert {copy["surfaces"][i]["name"] for i in np.unique(ids)} == {"asphalt", "gravel", "grass"}
    assert copy["obstacles_z"] == [0.5]


def test_the_outdoor_scenes_compile():
    for name in ("yard_outdoor", "test_track"):
        compiled = compile_scene(load_scene_yaml(f"sim/scenes/{name}.yaml"), robot_file="go2.xml")
        assert compiled.terrain is not None and compiled.xml.count('type="hfield"') >= 5


def test_raised_areas_and_ramps_are_boxes_whose_tops_match_the_heights():
    s = parse_terrain(spec(ramps=[{"x": [1, 3], "y": [-1, 1], "from_m": 0.0, "to_m": 0.4, "along": "-y"}]))
    t = build_terrain(s)
    boxes = {name: (centre, half, quat) for name, _, centre, half, quat in structures(t, s)}
    assert set(boxes) == {"ter_box_0", "ter_ramp_0"}
    (cx, cy, cz), (hx, hy, hz), _ = boxes["ter_box_0"]
    assert (cx, cy) == (9.0, -2.0) and cz + hz == pytest.approx(0.12)  # the top of the kerb box
    (cx, cy, cz), (hx, hy, hz), (w, qx, qy, qz) = boxes["ter_ramp_0"]
    theta = 2 * np.arcsin(qx)
    assert theta == pytest.approx(-np.arctan2(0.4, 2.0))  # rises towards -y
    top_centre = np.array([cx, cy, cz]) + hz * np.array([0, -np.sin(theta), np.cos(theta)])
    assert top_centre[2] == pytest.approx(0.2) and hy * 2 * np.cos(theta) == pytest.approx(2.0)
