"""A scene drawn from above (ADR-0028): what is where on the picture, checked on its pixels."""

import pytest
from PIL import Image

from spingi.assets import bundled_scenes
from spingi.scene_check import SceneWarning, check_scene
from spingi.scene_map import ALERT, PAPER, _bounds, _nice_step, draw_scene, write_map
from spingi.scenes import SceneFile, load_scene, parse_scene

RED_OBSTACLE = (160, 32, 32)
GREEN_OBSTACLE = (32, 160, 32)


def scene(**sections) -> SceneFile:
    base = {
        "locations": {"dock": {"pose": {"x": 0, "y": 0}}, "shelf": {"pose": {"x": 6, "y": 0}}},
        "obstacles": [
            {"x": 3, "y": 3, "w": 2, "d": 2, "color": "#a02020"},  # north
            {"x": 3, "y": -3, "w": 2, "d": 2, "color": "#20a020"},  # south
        ],
        "safety": {"geofence": {"x_min": -2, "x_max": 8, "y_min": -5, "y_max": 5}},
    }
    return parse_scene({**base, **sections})


def pixel_of(image: Image.Image, world: SceneFile, x: float, y: float, title: bool = False) -> tuple[int, int, int]:
    """The pixel where a world point is drawn, recomputed from the picture's own layout."""
    x0, y0, x1, y1 = _bounds(world)
    pad, header = 36, 44 if title else 12
    scale = min((image.width - 2 * pad) / (x1 - x0), (1800 - 2 * pad) / (y1 - y0))
    left = (image.width - (x1 - x0) * scale) / 2
    bottom = header + pad + (y1 - y0) * scale
    return image.getpixel((round(left + (x - x0) * scale), round(bottom - (y - y0) * scale)))


def colours(image: Image.Image) -> set[tuple[int, int, int]]:
    return {colour for _, colour in image.getcolors(maxcolors=1 << 20)}


def test_the_picture_is_the_width_asked_for_and_as_tall_as_the_scene_needs():
    image = draw_scene(scene(), width=1000)
    assert image.mode == "RGB" and image.width == 1000
    wide = parse_scene({"locations": {"a": {"pose": {"x": 0, "y": 0}}, "b": {"pose": {"x": 40, "y": 0}}}})
    assert draw_scene(wide, width=1000).height < image.height


def test_north_is_up_and_an_obstacle_has_its_own_colour_where_it_stands():
    """World y grows upward on the map: the opposite of the picture's rows."""
    world = scene()
    image = draw_scene(world)
    assert pixel_of(image, world, 3, 3.6) == RED_OBSTACLE
    assert pixel_of(image, world, 3, -3.6) == GREEN_OBSTACLE
    assert pixel_of(image, world, 6.9, 4.2) not in (RED_OBSTACLE, GREEN_OBSTACLE)  # open floor


def test_a_title_moves_the_map_down_not_the_world():
    world = scene()
    image = draw_scene(world, title="my warehouse")
    assert pixel_of(image, world, 3, 3.6, title=True) == RED_OBSTACLE


def test_warnings_are_marked_in_red_and_listed_under_the_map():
    world = scene()
    plain = draw_scene(world)
    assert ALERT not in colours(plain)
    crossing = SceneWarning(code="leg_crosses_obstacle", message="the leg crosses a box", where=[(0, 0), (6, 0)])
    marked = draw_scene(world, [crossing])
    assert pixel_of(marked, world, 1.5, 0) == ALERT  # the leg, drawn over the floor
    assert marked.height > plain.height  # room for the list
    assert ALERT in {marked.getpixel((x, marked.height - 30)) for x in range(36, 300)}  # its line of text


def test_more_warnings_than_fit_are_counted():
    many = [SceneWarning(code="near_geofence", message=f"warning {i}", where=[(i, 0)]) for i in range(12)]
    few = many[:8]
    assert draw_scene(scene(), many).height == draw_scene(scene(), few).height + 22  # one line: "and 4 more"


def test_a_ground_is_drawn_in_its_surfaces():
    ground = {
        "extent": {"x_min": -3, "x_max": 9, "y_min": -6, "y_max": 6},
        "surface": "asphalt",
        "regions": [{"surface": "grass", "x": [5.5, 9], "y": [2, 6]}],
        "ramps": [{"x": [5.5, 7.5], "y": [-6, -4], "to_m": 0.3}],
        "raised": [{"x": [7.5, 9], "y": [-6, -4], "height_m": 0.3}],
    }
    world = scene(terrain=ground)
    image = draw_scene(world)
    asphalt, grass = pixel_of(image, world, -1.3, 2.4), pixel_of(image, world, 8.4, 5.3)
    assert asphalt != grass and grass[1] > grass[0] and grass[1] > grass[2]  # green
    assert asphalt[0] == asphalt[1] and abs(asphalt[0] - asphalt[2]) < 8  # grey


def test_a_scene_with_nothing_in_it_still_draws():
    image = draw_scene(parse_scene({}))
    assert image.width == 1400 and PAPER in colours(image)


@pytest.mark.parametrize("name", sorted(bundled_scenes()))
def test_every_sample_is_drawn_with_its_warnings(name, tmp_path):
    world = load_scene(bundled_scenes()[name])
    out = write_map(world, tmp_path / "maps" / f"{name}.png", check_scene(world), title=name)
    with Image.open(out) as image:
        assert image.format == "PNG" and image.width == 1400 and 300 < image.height <= 2200
    assert out.stat().st_size < 400_000


@pytest.mark.parametrize(("target", "step"), [(0.7, 1), (1.3, 2), (3.0, 5), (7, 10), (12, 20), (0.03, 0.05)])
def test_grid_and_scale_use_round_lengths(target, step):
    assert _nice_step(target) == pytest.approx(step)
