"""A scene seen from above, as a picture (ADR-0028).

A scene is numbers in a YAML file, and a wrong number looks like any other number. Drawn, a shelf in the middle of
the aisle, a stop that faces the wall or a tag behind the robot are seen at a glance, by a person and by an agent
that can look at an image. The map shows the ground's surfaces, ramps and raised areas, the geofence, obstacles,
locations with their heading, objects, landmarks with the way they face, the robot's start and the routes; the
warnings of `scene_check` are marked in red and listed under the map.

Drawn with Pillow (the `sim` extra): no OpenGL, no MuJoCo model.
"""

from __future__ import annotations

import math
import textwrap
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

from spingi.scene_check import SceneWarning
from spingi.scenes import SceneFile

if TYPE_CHECKING:
    from PIL import Image, ImageDraw, ImageFont

Rgb = tuple[int, int, int]
Box = tuple[float, float, float, float]

PAPER: Rgb = (247, 247, 244)
INK: Rgb = (35, 38, 46)
FAINT: Rgb = (214, 214, 208)
FENCE: Rgb = (200, 60, 50)
ROUTE: Rgb = (60, 105, 200)
PLACE: Rgb = (40, 90, 190)
TAG: Rgb = (20, 20, 20)
ALERT: Rgb = (214, 40, 40)
OBSTACLE: Rgb = (140, 140, 153)
OBJECT: Rgb = (204, 64, 64)
MARGIN_M = 1.0  # of world drawn around the scene
MAX_SIDE_PX = 1800
MAX_LISTED = 8  # warnings written under the map; the others are counted


def write_map(
    scene: SceneFile, path: Path | str, warnings: Sequence[SceneWarning] = (), title: str = "", width: int = 1400
) -> Path:
    """Draws the scene and saves it as a PNG."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    draw_scene(scene, warnings, title=title, width=width).save(path, format="PNG", optimize=True)
    return path


def draw_scene(
    scene: SceneFile, warnings: Sequence[SceneWarning] = (), title: str = "", width: int = 1400
) -> Image.Image:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(f"drawing a map needs Pillow ({exc}); install the sim extra") from exc

    world = _bounds(scene)
    scale = min((width - 2 * _PAD) / (world[2] - world[0]), (MAX_SIDE_PX - 2 * _PAD) / (world[3] - world[1]))
    map_w, map_h = (world[2] - world[0]) * scale, (world[3] - world[1]) * scale
    font = ImageFont.load_default(size=15)
    small = ImageFont.load_default(size=12)
    notes = _notes(warnings, chars=int((width - 2 * _PAD) / 7.4))
    header = 44 if title else 12
    height = int(header + map_h + 2 * _PAD + 22 * len(notes) + (16 if notes else 0))
    image = Image.new("RGB", (width, height), PAPER)
    pen = _Pen(ImageDraw.Draw(image), world, scale, (width - map_w) / 2, header + _PAD + map_h, font, small)

    if title:
        pen.draw.text((_PAD, 12), title, fill=INK, font=ImageFont.load_default(size=20))
    _ground(pen, scene)
    _grid(pen)
    _fence(pen, scene)
    _obstacles(pen, scene)
    _routes(pen, scene)
    _landmarks(pen, scene)
    _objects(pen, scene)
    _locations(pen, scene)
    _start(pen, scene)
    pen.write_labels()
    _alerts(pen, warnings)
    _scale_bar(pen, right=width - _PAD, y=header + 8)
    y = header + map_h + 2 * _PAD
    for line in notes:
        pen.draw.text((_PAD, y), line, fill=ALERT if line[:1].isdigit() else INK, font=font)
        y += 22
    return image


_PAD = 36


class _Pen:
    """Draws in metres: turns world coordinates (x right, y up) into pixels, and keeps labels off one another."""

    def __init__(
        self,
        draw: ImageDraw.ImageDraw,
        world: Box,
        scale: float,
        left: float,
        bottom: float,
        font: ImageFont.FreeTypeFont,
        small: ImageFont.FreeTypeFont,
    ) -> None:
        self.draw, self.world, self.scale, self.left, self.bottom = draw, world, scale, left, bottom
        self.font, self.small = font, small
        self._taken: list[Box] = []
        self._labels: list[tuple[bool, float, float, str, Rgb, bool, float]] = []

    def px(self, x: float, y: float) -> tuple[float, float]:
        return self.left + (x - self.world[0]) * self.scale, self.bottom - (y - self.world[1]) * self.scale

    def rect(self, x0: float, y0: float, x1: float, y1: float, **style) -> None:
        (ax, ay), (bx, by) = self.px(x0, y1), self.px(x1, y0)
        self.draw.rectangle((ax, ay, bx, by), **style)

    def line(self, points: Sequence[tuple[float, float]], fill: Rgb, width: int = 2, dash: float = 0.0) -> None:
        pixels = [self.px(x, y) for x, y in points]
        for a, b in pairwise(pixels):
            if dash:
                _dashed(self.draw, a, b, fill, width, dash)
            else:
                self.draw.line((*a, *b), fill=fill, width=width)

    def arrow(self, x: float, y: float, angle: float, length_px: float, fill: Rgb, width: int = 2) -> None:
        """From a point, along a world angle (counter-clockwise from +x), with a head."""
        ax, ay = self.px(x, y)
        bx, by = ax + length_px * math.cos(angle), ay - length_px * math.sin(angle)
        self.draw.line((ax, ay, bx, by), fill=fill, width=width)
        for turn in (2.6, -2.6):
            hx = bx + 7 * math.cos(angle + turn)
            hy = by - 7 * math.sin(angle + turn)
            self.draw.line((bx, by, hx, hy), fill=fill, width=width)

    def label(self, x: float, y: float, text: str, fill: Rgb = INK, small: bool = False, gap: float = 9) -> None:
        """Text next to a point. Written by `write_labels` once every shape is drawn, so nothing covers it."""
        self._labels.append((not small, x, y, text, fill, small, gap))

    def write_labels(self) -> None:
        """The names first, then the smaller notes; each in the first of four places that covers no other label."""
        for _, x, y, text, fill, small, gap in sorted(self._labels, key=lambda item: not item[0]):
            font = self.small if small else self.font
            cx, cy = self.px(x, y)
            _, top, w, h = font.getbbox(text)
            places = [
                (cx + gap, cy - h - 2),
                (cx + gap, cy + 4),
                (cx - gap - w, cy - h - 2),
                (cx - w / 2, cy + gap + 2),
            ]
            spot = next((p for p in places if not self._covers((p[0], p[1], p[0] + w, p[1] + h))), places[0])
            self._taken.append((spot[0] - 2, spot[1] - 2, spot[0] + w + 2, spot[1] + h + 2))
            self.draw.rectangle((spot[0] - 2, spot[1] + top - 1, spot[0] + w + 2, spot[1] + h + 1), fill=PAPER)
            self.draw.text(spot, text, fill=fill, font=font)
        self._labels.clear()

    def _covers(self, box: Box) -> bool:
        return any(box[0] < t[2] and t[0] < box[2] and box[1] < t[3] and t[1] < box[3] for t in self._taken)


# --- layers ------------------------------------------------------------------------------------------------------
def _ground(pen: _Pen, scene: SceneFile) -> None:
    terrain = scene.terrain
    if terrain is None:
        pen.rect(*pen.world, fill=(236, 236, 232))
        return
    e = terrain.extent
    pen.rect(e.x_min, e.y_min, e.x_max, e.y_max, fill=_surface(terrain.surface))
    for region in terrain.regions:
        pen.rect(region.x[0], region.y[0], region.x[1], region.y[1], fill=_surface(region.surface))
    for area in terrain.raised:
        pen.rect(area.x[0], area.y[0], area.x[1], area.y[1], outline=(90, 90, 96), width=2)
        pen.label(area.x[0], area.y[1], f"+{area.height_m:g} m", fill=(70, 70, 76), small=True, gap=4)
    for ramp in terrain.ramps:
        pen.rect(ramp.x[0], ramp.y[0], ramp.x[1], ramp.y[1], fill=(196, 190, 178), outline=(90, 90, 96), width=2)
        angle = {"x": 0.0, "-x": math.pi, "y": math.pi / 2, "-y": -math.pi / 2}[ramp.along]
        cx, cy = (ramp.x[0] + ramp.x[1]) / 2, (ramp.y[0] + ramp.y[1]) / 2
        pen.arrow(cx, cy, angle, 26, (70, 70, 76))
        pen.label(
            ramp.x[0], ramp.y[1], f"ramp {ramp.from_m:g} to {ramp.to_m:g} m", fill=(70, 70, 76), small=True, gap=4
        )


def _grid(pen: _Pen) -> None:
    x0, y0, x1, y1 = pen.world
    step = _nice_step(max(x1 - x0, y1 - y0) / 12)
    x = math.ceil(x0 / step) * step
    while x <= x1:
        pen.line([(x, y0), (x, y1)], FAINT, 1)
        px, py = pen.px(x, y0)
        pen.draw.text((px + 3, py - 15), f"{x:g}", fill=(120, 120, 116), font=pen.small)
        x += step
    y = math.ceil(y0 / step) * step
    while y <= y1:
        pen.line([(x0, y), (x1, y)], FAINT, 1)
        px, py = pen.px(x0, y)
        pen.draw.text((px + 3, py - 15), f"{y:g}", fill=(120, 120, 116), font=pen.small)
        y += step


def _fence(pen: _Pen, scene: SceneFile) -> None:
    f = scene.safety.geofence
    if f is None:
        return
    corners = [(f.x_min, f.y_min), (f.x_max, f.y_min), (f.x_max, f.y_max), (f.x_min, f.y_max), (f.x_min, f.y_min)]
    pen.line(corners, FENCE, 2, dash=9)
    pen.label(f.x_min, f.y_max, "geofence", fill=FENCE, small=True, gap=4)


def _obstacles(pen: _Pen, scene: SceneFile) -> None:
    for index, o in enumerate(scene.obstacles):
        fill = _hex(o.color) if o.color else OBSTACLE
        pen.rect(o.x - o.w / 2, o.y - o.d / 2, o.x + o.w / 2, o.y + o.d / 2, fill=fill, outline=INK, width=1)
        cx, cy = pen.px(o.x, o.y)
        text = str(index)
        _, _, w, h = pen.small.getbbox(text)
        if o.w * pen.scale > w + 4 and o.d * pen.scale > h + 4:
            ink = INK if sum(fill) > 380 else (255, 255, 255)
            pen.draw.text((cx - w / 2, cy - h / 2 - 1), text, fill=ink, font=pen.small)


def _routes(pen: _Pen, scene: SceneFile) -> None:
    for key, waypoints in scene.routes.items():
        src, _, dst = key.partition("->")
        poses = [scene.locations[n].pose for n in (src, *waypoints, dst)]
        pen.line([(p.x, p.y) for p in poses], ROUTE, 1, dash=5)


def _landmarks(pen: _Pen, scene: SceneFile) -> None:
    for lm in scene.landmarks:
        cx, cy = pen.px(lm.x, lm.y)
        pen.draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 255, 255), outline=TAG, width=2)
        pen.draw.rectangle((cx - 2, cy - 2, cx + 2, cy + 2), fill=TAG)
        pen.arrow(lm.x, lm.y, lm.facing, 16, TAG, 1)
        pen.label(lm.x, lm.y, f"tag {lm.marker_id}", fill=TAG, small=True)


def _objects(pen: _Pen, scene: SceneFile) -> None:
    for oid, obj in scene.objects.items():
        if obj.pose is None:
            continue
        cx, cy = pen.px(obj.pose.x, obj.pose.y)
        half_w, half_d = max(obj.size[0] * pen.scale / 2, 4), max(obj.size[1] * pen.scale / 2, 4)
        fill = _hex(obj.color) if obj.color else OBJECT
        pen.draw.rectangle((cx - half_w, cy - half_d, cx + half_w, cy + half_d), fill=fill, outline=INK, width=1)
        pen.label(obj.pose.x, obj.pose.y, f"{oid} ({obj.cls})", fill=(150, 30, 30), small=True)


def _locations(pen: _Pen, scene: SceneFile) -> None:
    for name, loc in scene.locations.items():
        cx, cy = pen.px(loc.pose.x, loc.pose.y)
        r = max(loc.tolerance_m * pen.scale, 5)
        pen.draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=PLACE, width=2)
        pen.draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=PLACE)
        pen.arrow(loc.pose.x, loc.pose.y, loc.pose.yaw, r + 16, PLACE)
        pen.label(loc.pose.x, loc.pose.y, name, fill=PLACE)


def _start(pen: _Pen, scene: SceneFile) -> None:
    pose = scene.robot.pose
    cx, cy = pen.px(pose.x, pose.y)
    tip = (cx + 11 * math.cos(pose.yaw), cy - 11 * math.sin(pose.yaw))
    left = (cx + 8 * math.cos(pose.yaw + 2.5), cy - 8 * math.sin(pose.yaw + 2.5))
    right = (cx + 8 * math.cos(pose.yaw - 2.5), cy - 8 * math.sin(pose.yaw - 2.5))
    pen.draw.polygon([tip, left, right], fill=(30, 150, 90), outline=INK)
    pen.label(pose.x, pose.y, "start", fill=(20, 110, 65), small=True)


def _alerts(pen: _Pen, warnings: Sequence[SceneWarning]) -> None:
    for number, warning in enumerate(warnings, start=1):
        if not warning.where:
            continue
        if len(warning.where) >= 2:
            pen.line(warning.where, ALERT, 3)
        x, y = warning.where[0] if len(warning.where) == 1 else _middle(warning.where[0], warning.where[1])
        cx, cy = pen.px(x, y)
        if len(warning.where) == 1:
            pen.draw.ellipse((cx - 13, cy - 13, cx + 13, cy + 13), outline=ALERT, width=3)
        text = str(number)
        _, _, w, h = pen.font.getbbox(text)
        bx, by = cx + 12, cy - 26
        pen.draw.ellipse((bx - 4, by - 2, bx + w + 4, by + h + 4), fill=ALERT)
        pen.draw.text((bx, by), text, fill=(255, 255, 255), font=pen.font)


def _scale_bar(pen: _Pen, right: float, y: float) -> None:
    """A round length above the map's top right corner, with its measure."""
    x0, _, x1, _ = pen.world
    length = _nice_step((x1 - x0) / 6)
    left = right - length * pen.scale
    pen.draw.line((left, y, right, y), fill=INK, width=3)
    for x in (left, right):
        pen.draw.line((x, y - 5, x, y + 5), fill=INK, width=2)
    text = f"{length:g} m"
    _, _, w, _ = pen.small.getbbox(text)
    pen.draw.text((left - w - 8, y - 8), text, fill=INK, font=pen.small)


# --- helpers -----------------------------------------------------------------------------------------------------
def _bounds(scene: SceneFile) -> Box:
    """The part of the world worth drawing: everything the scene mentions, and a metre around it."""
    xs: list[float] = [scene.robot.pose.x]
    ys: list[float] = [scene.robot.pose.y]
    for loc in scene.locations.values():
        xs.append(loc.pose.x)
        ys.append(loc.pose.y)
    for o in scene.obstacles:
        xs += [o.x - o.w / 2, o.x + o.w / 2]
        ys += [o.y - o.d / 2, o.y + o.d / 2]
    for obj in scene.objects.values():
        if obj.pose is not None:
            xs.append(obj.pose.x)
            ys.append(obj.pose.y)
    for lm in scene.landmarks:
        xs.append(lm.x)
        ys.append(lm.y)
    for box in (scene.safety.geofence, scene.terrain.extent if scene.terrain else None):
        if box is not None:
            xs += [box.x_min, box.x_max]
            ys += [box.y_min, box.y_max]
    return min(xs) - MARGIN_M, min(ys) - MARGIN_M, max(xs) + MARGIN_M, max(ys) + MARGIN_M


def _notes(warnings: Sequence[SceneWarning], chars: int) -> list[str]:
    """The warnings as numbered lines, wrapped to the picture's width."""
    lines: list[str] = []
    for number, warning in enumerate(warnings[:MAX_LISTED], start=1):
        wrapped = textwrap.wrap(f"{number}. {warning.message}", width=max(chars, 40), subsequent_indent="   ")
        lines += wrapped
    if len(warnings) > MAX_LISTED:
        lines.append(f"and {len(warnings) - MAX_LISTED} more warnings")
    return lines


def _surface(name: str) -> Rgb:
    """The simulator's colour for a surface, lightened so that labels stay readable on it."""
    from spingi.adapters.sim_mujoco.terrain import SURFACES

    return tuple(int(255 * (0.45 + 0.55 * c)) for c in SURFACES[name].rgb)  # type: ignore[return-value]


def _hex(color: str) -> Rgb:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _nice_step(target: float) -> float:
    """The round length (1, 2 or 5 times a power of ten) nearest above `target` metres."""
    power = 10 ** math.floor(math.log10(max(target, 1e-6)))
    return next(m * power for m in (1, 2, 5, 10) if m * power >= target)


def _middle(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def _dashed(
    draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], fill: Rgb, width: int, dash: float
) -> None:
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length == 0:
        return
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    travelled = 0.0
    while travelled < length:
        end = min(travelled + dash, length)
        draw.line(
            (a[0] + ux * travelled, a[1] + uy * travelled, a[0] + ux * end, a[1] + uy * end), fill=fill, width=width
        )
        travelled += 2 * dash
