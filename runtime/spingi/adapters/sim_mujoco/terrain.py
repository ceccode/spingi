"""Ground that is not a flat floor: surfaces with their own grip and roughness, ramps and raised areas (ADR-0015).

The `terrain:` section of a scene describes it; this module turns it into a height map, cuts the map into one MuJoCo
height field per rectangle of uniform surface (each with that surface's friction and look) and exports a lighter copy
for the viewer. The same seed gives the same ground on every run.

    terrain:
      extent: { x_min: -2, x_max: 20, y_min: -6, y_max: 6 }   # must cover the geofence
      cell_m: 0.1             # grid spacing, 0.08 to 0.25 m
      seed: 3                 # roughness noise
      surface: asphalt        # everywhere, unless a region says otherwise
      regions:                # later regions win where they overlap
        - { surface: gravel, x: [4, 9], y: [-5, 0] }
        - { surface: dirt, x: [9, 16], y: [-5, 0], roughness_m: 0.03 }
      ramps:                  # height changes linearly across the rectangle, along an axis
        - { x: [12, 15], y: [2, 4], from_m: 0.0, to_m: 0.45, along: x }
      raised:                 # flat areas at a height: kerbs, pavements, loading docks
        - { x: [15, 19], y: [1, 5], height_m: 0.45 }

Roughness is a standard deviation in metres; the bumps a robot meets are about two to three times it. Raised areas
and ramps are built structures: flat on top, with vertical sides, simulated as solid boxes (a step inside a height
field is a steep slope a foot can sink into); the height fields carry the ground's roughness around them.
"""

from __future__ import annotations

import base64
import itertools
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from spingi.adapters.sim_mujoco.values import SceneError, interval, num


@dataclass(frozen=True)
class Surface:
    label: str
    rgb: tuple[float, float, float]
    speck: tuple[float, float, float]  # colour of the grain drawn over the base colour
    friction: float  # sliding friction: the ground decides the grip, the feet keep their own contact softness
    roughness_m: float  # standard deviation of the height noise
    wavelength_m: float  # typical size of a bump
    grain: float  # share of the texture covered by the speck colour


SURFACES: dict[str, Surface] = {
    "floor": Surface("indoor floor, sealed concrete", (0.60, 0.61, 0.64), (0.52, 0.53, 0.57), 0.8, 0.0, 1.0, 0.04),
    "concrete": Surface("outdoor concrete", (0.66, 0.65, 0.62), (0.55, 0.54, 0.51), 0.85, 0.002, 1.0, 0.10),
    "asphalt": Surface("asphalt", (0.19, 0.19, 0.21), (0.34, 0.34, 0.35), 0.9, 0.003, 0.8, 0.18),
    "gravel": Surface("gravel", (0.58, 0.55, 0.50), (0.38, 0.36, 0.33), 0.6, 0.008, 0.3, 0.45),
    "dirt": Surface("dirt track", (0.47, 0.36, 0.25), (0.35, 0.26, 0.18), 0.65, 0.015, 0.6, 0.30),
    "grass": Surface("grass", (0.34, 0.46, 0.25), (0.25, 0.36, 0.18), 0.5, 0.01, 0.5, 0.40),
}

# Feet are spheres of 1 to 2 cm; on finer height fields they catch on the cell edges and the walking fails for reasons
# that are not the ground's (measured, ADR-0015). Finer grain than the cell, like gravel stones, is in the grip.
MIN_CELL_M, MAX_CELL_M = 0.08, 0.25
DEFAULT_CELL_M = 0.1
MAX_SAMPLES = 2_000_000  # a larger grid is a mistake in the scene, not a terrain
BLEND_M = 0.25  # surfaces fade into each other's roughness over this distance: no seams of height at the borders
VIEWER_CELL_M = 0.1  # the copy written into episodes for the viewer


@dataclass
class TerrainSpec:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    cell_m: float
    seed: int
    surface: str
    regions: list[tuple[str, tuple[float, float], tuple[float, float], float | None]]
    ramps: list[tuple[tuple[float, float], tuple[float, float], float, float, str]]
    raised: list[tuple[tuple[float, float], tuple[float, float], float]]


@dataclass
class TerrainMap:
    """Heights on a grid: `heights[row, col]` is the ground at x = x0 + col * cell, y = y0 + row * cell, built
    structures included; `ground` is the same without them, what the height fields carry."""

    x0: float
    y0: float
    cell_m: float
    heights: np.ndarray
    labels: np.ndarray  # index into `surfaces` per sample
    surfaces: list[str]
    ground: np.ndarray

    @property
    def rows(self) -> int:
        return int(self.heights.shape[0])

    @property
    def cols(self) -> int:
        return int(self.heights.shape[1])

    def height_at(self, x: float, y: float) -> float:
        """Ground height at a point, bilinear between samples; outside the grid, the nearest edge."""
        fx = min(max((x - self.x0) / self.cell_m, 0.0), self.cols - 1.0)
        fy = min(max((y - self.y0) / self.cell_m, 0.0), self.rows - 1.0)
        c0, r0 = min(int(fx), self.cols - 2), min(int(fy), self.rows - 2)
        u, v = fx - c0, fy - r0
        h = self.heights
        top = h[r0, c0] * (1 - u) + h[r0, c0 + 1] * u
        bottom = h[r0 + 1, c0] * (1 - u) + h[r0 + 1, c0 + 1] * u
        return float(top * (1 - v) + bottom * v)

    def max_height_over(self, x: tuple[float, float], y: tuple[float, float]) -> float:
        """Highest ground under a rectangle: an obstacle stands on it."""
        c0 = int(np.clip(np.floor((x[0] - self.x0) / self.cell_m), 0, self.cols - 1))
        c1 = int(np.clip(np.ceil((x[1] - self.x0) / self.cell_m), 0, self.cols - 1))
        r0 = int(np.clip(np.floor((y[0] - self.y0) / self.cell_m), 0, self.rows - 1))
        r1 = int(np.clip(np.ceil((y[1] - self.y0) / self.cell_m), 0, self.rows - 1))
        return float(self.heights[r0 : r1 + 1, c0 : c1 + 1].max())

    def surface_at(self, x: float, y: float) -> str:
        c = int(np.clip(round((x - self.x0) / self.cell_m), 0, self.cols - 1))
        r = int(np.clip(round((y - self.y0) / self.cell_m), 0, self.rows - 1))
        return self.surfaces[int(self.labels[r, c])]


# --- parsing -----------------------------------------------------------------------------------------------------
def parse_terrain(raw: Any) -> TerrainSpec:
    if not isinstance(raw, dict):
        raise SceneError("terrain must be a mapping")
    ext = raw.get("extent")
    if not isinstance(ext, dict):
        raise SceneError("terrain.extent is required: { x_min, x_max, y_min, y_max }")
    x_min, x_max = interval([ext.get("x_min"), ext.get("x_max")], "terrain.extent x")
    y_min, y_max = interval([ext.get("y_min"), ext.get("y_max")], "terrain.extent y")
    cell = num(raw.get("cell_m", DEFAULT_CELL_M), "terrain.cell_m", positive=True)
    if not MIN_CELL_M <= cell <= MAX_CELL_M:
        raise SceneError(f"terrain.cell_m must be between {MIN_CELL_M} and {MAX_CELL_M} m, got {cell}")
    if ((x_max - x_min) / cell + 1) * ((y_max - y_min) / cell + 1) > MAX_SAMPLES:
        raise SceneError(f"terrain grid larger than {MAX_SAMPLES} samples: use a larger cell_m or a smaller extent")
    seed = raw.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise SceneError(f"terrain.seed must be a non-negative integer, got {seed!r}")
    base = _surface(raw.get("surface", "floor"), "terrain.surface")
    regions = []
    for i, reg in enumerate(_list(raw.get("regions"), "terrain.regions")):
        what = f"terrain.regions[{i}]"
        rough = reg.get("roughness_m")
        if rough is not None and (num(rough, f"{what}.roughness_m") < 0 or rough > 0.1):
            raise SceneError(f"{what}.roughness_m must be between 0 and 0.1 m, got {rough!r}")
        regions.append(
            (
                _surface(reg.get("surface"), f"{what}.surface"),
                interval(reg.get("x"), f"{what}.x"),
                interval(reg.get("y"), f"{what}.y"),
                None if rough is None else float(rough),
            )
        )
    ramps = []
    for i, ramp in enumerate(_list(raw.get("ramps"), "terrain.ramps")):
        what = f"terrain.ramps[{i}]"
        along = ramp.get("along", "x")
        if along not in ("x", "y", "-x", "-y"):
            raise SceneError(f"{what}.along must be x, y, -x or -y, got {along!r}")
        h0, h1 = _height(ramp.get("from_m", 0.0), f"{what}.from_m"), _height(ramp.get("to_m"), f"{what}.to_m")
        (x0, x1), (y0, y1) = interval(ramp.get("x"), f"{what}.x"), interval(ramp.get("y"), f"{what}.y")
        length = (x1 - x0) if along.endswith("x") else (y1 - y0)
        if abs(h1 - h0) / length > 1.0:
            raise SceneError(f"{what} is steeper than 45 degrees: make it longer or use a raised area")
        ramps.append(((x0, x1), (y0, y1), h0, h1, along))
    raised = []
    for i, area in enumerate(_list(raw.get("raised"), "terrain.raised")):
        what = f"terrain.raised[{i}]"
        raised.append(
            (
                interval(area.get("x"), f"{what}.x"),
                interval(area.get("y"), f"{what}.y"),
                _height(area.get("height_m"), f"{what}.height_m"),
            )
        )
    return TerrainSpec(x_min, x_max, y_min, y_max, cell, seed, base, regions, ramps, raised)


def _list(value: Any, what: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise SceneError(f"{what} must be a list of mappings")
    return value


def _surface(value: Any, what: str) -> str:
    if value not in SURFACES:
        raise SceneError(f"{what} must be one of {', '.join(SURFACES)}, got {value!r}")
    return value


def _height(value: Any, what: str) -> float:
    """Built structures stand on the ground: from 0 to 3 m. Pits and ditches are not supported."""
    h = num(value, what)
    if not 0.0 <= h <= 3.0:
        raise SceneError(f"{what} must be between 0 and 3 m, got {h}")
    return h


# --- generation --------------------------------------------------------------------------------------------------
def build_terrain(spec: TerrainSpec) -> TerrainMap:
    cols = round((spec.x_max - spec.x_min) / spec.cell_m) + 1
    rows = round((spec.y_max - spec.y_min) / spec.cell_m) + 1
    xs = spec.x_min + np.arange(cols) * spec.cell_m
    ys = spec.y_min + np.arange(rows) * spec.cell_m
    surfaces = list(SURFACES)
    labels = np.full((rows, cols), surfaces.index(spec.surface), dtype=np.uint8)
    rough = np.full((rows, cols), np.nan)
    for name, (x0, x1), (y0, y1), roughness in spec.regions:
        cmask = (xs >= x0 - 1e-9) & (xs <= x1 + 1e-9)
        rmask = (ys >= y0 - 1e-9) & (ys <= y1 + 1e-9)
        labels[np.ix_(rmask, cmask)] = surfaces.index(name)
        rough[np.ix_(rmask, cmask)] = np.nan if roughness is None else roughness

    # Roughness: each surface's noise, weighted by its blurred share of the neighbourhood (no seams at the borders).
    radius = max(1, round(BLEND_M / spec.cell_m))
    noise = np.zeros((rows, cols))
    weight = np.zeros((rows, cols))
    for idx, name in enumerate(surfaces):
        mask = labels == idx
        if not mask.any():
            continue
        s = SURFACES[name]
        amp = np.where(np.isnan(rough), s.roughness_m, rough) * mask
        if not amp.any():
            weight += _blur(mask.astype(float), radius)
            continue
        rng = np.random.default_rng([spec.seed, idx])
        n = _value_noise(rows, cols, spec.cell_m, s.wavelength_m, rng)
        fine = max(s.wavelength_m / 3, 2.5 * spec.cell_m)  # never below what the grid can carry
        n += 0.4 * _value_noise(rows, cols, spec.cell_m, fine, rng)
        n /= max(float(n.std()), 1e-9)
        share = _blur(mask.astype(float), radius)
        noise += _blur(amp, radius) * n
        weight += share
    noise /= np.maximum(weight, 1e-9)

    # Built structures: flat tops at their height, no roughness on them.
    structure = np.full((rows, cols), -np.inf)
    for (x0, x1), (y0, y1), h in spec.raised:
        cmask = (xs >= x0 - 1e-9) & (xs <= x1 + 1e-9)
        rmask = (ys >= y0 - 1e-9) & (ys <= y1 + 1e-9)
        structure[np.ix_(rmask, cmask)] = np.maximum(structure[np.ix_(rmask, cmask)], h)
    for (x0, x1), (y0, y1), h0, h1, along in spec.ramps:
        cmask = (xs >= x0 - 1e-9) & (xs <= x1 + 1e-9)
        rmask = (ys >= y0 - 1e-9) & (ys <= y1 + 1e-9)
        gx, gy = np.meshgrid(xs[cmask], ys[rmask])
        u = (gx - x0) / (x1 - x0) if along.endswith("x") else (gy - y0) / (y1 - y0)
        if along.startswith("-"):
            u = 1.0 - u
        structure[np.ix_(rmask, cmask)] = np.maximum(structure[np.ix_(rmask, cmask)], h0 + (h1 - h0) * u)
    built = np.isfinite(structure)
    ground = np.where(built, np.minimum(noise, 0.0), noise)  # under a structure, out of its way
    heights = np.where(built, np.maximum(structure, noise), noise)
    return TerrainMap(spec.x_min, spec.y_min, spec.cell_m, heights, labels, surfaces, ground)


def _value_noise(rows: int, cols: int, cell: float, wavelength: float, rng: np.random.Generator) -> np.ndarray:
    """Smooth random bumps of about `wavelength` metres: random values on a coarse grid, smoothly interpolated."""
    step = wavelength / cell
    gy, gx = int(np.ceil(rows / step)) + 2, int(np.ceil(cols / step)) + 2
    grid = rng.standard_normal((gy, gx))
    y, x = np.arange(rows) / step, np.arange(cols) / step
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    fy, fx = y - y0, x - x0
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    a, b = grid[np.ix_(y0, x0)], grid[np.ix_(y0, x0 + 1)]
    c, d = grid[np.ix_(y0 + 1, x0)], grid[np.ix_(y0 + 1, x0 + 1)]
    top = a + (b - a) * fx[None, :]
    bottom = c + (d - c) * fx[None, :]
    return top + (bottom - top) * fy[:, None]


def _blur(a: np.ndarray, r: int) -> np.ndarray:
    """Box blur of radius r samples, separable, edges repeated."""
    out = a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
        hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
        out = (hi - lo) / (2 * r + 1)
    return out


# --- MuJoCo ------------------------------------------------------------------------------------------------------
@dataclass
class Patch:
    """One height field: a rectangle of the grid (shared border samples with its neighbours) of a single surface."""

    name: str
    surface: str
    r0: int
    r1: int
    c0: int
    c1: int


def patches(terrain: TerrainMap, spec: TerrainSpec) -> list[Patch]:
    """Rectangles of uniform surface covering the grid. Their edges are the region edges, snapped to the grid."""

    def breaks(edges: list[float], origin: float, n: int) -> list[int]:
        idx = {0, n - 1}
        for e in edges:
            i = round((e - origin) / terrain.cell_m)
            if 0 < i < n - 1:
                idx.add(i)
        return sorted(idx)

    cb = breaks([v for _, x, _, _ in spec.regions for v in x], terrain.x0, terrain.cols)
    rb = breaks([v for _, _, y, _ in spec.regions for v in y], terrain.y0, terrain.rows)
    out: list[Patch] = []
    for r0, r1 in itertools.pairwise(rb):
        run: list[int] | None = None  # [c0, c1, label] of the patch being extended along the row band
        for c0, c1 in itertools.pairwise(cb):
            label = int(terrain.labels[(r0 + r1) // 2, (c0 + c1) // 2])
            if run is not None and run[2] == label:
                run[1] = c1
                continue
            if run is not None:
                out.append(Patch(f"ter_{len(out)}", terrain.surfaces[run[2]], r0, r1, run[0], run[1]))
            run = [c0, c1, label]
        if run is not None:
            out.append(Patch(f"ter_{len(out)}", terrain.surfaces[run[2]], r0, r1, run[0], run[1]))
    return out


STRUCTURE_DEPTH_M = 0.3  # how far a structure's box reaches below the ground


def structures(terrain: TerrainMap, spec: TerrainSpec) -> list[tuple[str, str, tuple, tuple, tuple]]:
    """Raised areas and ramps as boxes: (name, surface, centre, half sizes, quaternion w x y z)."""
    out = []
    t = STRUCTURE_DEPTH_M
    for i, ((x0, x1), (y0, y1), h) in enumerate(spec.raised):
        centre = ((x0 + x1) / 2, (y0 + y1) / 2, (h - t) / 2)
        out.append((f"ter_box_{i}", centre, ((x1 - x0) / 2, (y1 - y0) / 2, (h + t) / 2), (1.0, 0.0, 0.0, 0.0)))
    for i, ((x0, x1), (y0, y1), h0, h1, along) in enumerate(spec.ramps):
        rise = h1 - h0 if not along.startswith("-") else h0 - h1  # height gained going towards +x (+y)
        xc, yc, top = (x0 + x1) / 2, (y0 + y1) / 2, (h0 + h1) / 2
        if along.endswith("x"):
            theta = math.atan2(rise, x1 - x0)
            half = ((x1 - x0) / 2 / math.cos(theta), (y1 - y0) / 2, t / 2)
            centre = (xc + t / 2 * math.sin(theta), yc, top - t / 2 * math.cos(theta))
            quat = (math.cos(-theta / 2), 0.0, math.sin(-theta / 2), 0.0)  # about y: the top rises along +x
        else:
            theta = math.atan2(rise, y1 - y0)
            half = ((x1 - x0) / 2, (y1 - y0) / 2 / math.cos(theta), t / 2)
            centre = (xc, yc - t / 2 * math.sin(theta), top - t / 2 * math.cos(theta))
            quat = (math.cos(theta / 2), math.sin(theta / 2), 0.0, 0.0)  # about x: the top rises along +y
        out.append((f"ter_ramp_{i}", centre, half, quat))
    return [(name, terrain.surface_at(c[0], c[1]), c, hs, q) for name, c, hs, q in out]


def mjcf(terrain: TerrainMap, spec: TerrainSpec) -> tuple[list[str], list[str]]:
    """(asset lines, worldbody lines) for the terrain. Heights are filled in after loading (`fill_heights`)."""
    zmin, zmax = float(terrain.ground.min()), float(terrain.ground.max())
    span = max(zmax - zmin, 1e-3)
    assets, bodies = [], []
    used = sorted({p.surface for p in patches(terrain, spec)} | {s for _, s, *_ in structures(terrain, spec)})
    for name in used:
        s = SURFACES[name]
        assets.append(
            f'    <texture name="surf_{name}" type="2d" builtin="flat" mark="random" random="{s.grain}" '
            f'rgb1="{_rgb(s.rgb)}" markrgb="{_rgb(s.speck)}" width="96" height="96"/>'
        )
        assets.append(
            f'    <material name="surf_{name}" texture="surf_{name}" texuniform="true" texrepeat="2 2" '
            'reflectance="0" specular="0.1" shininess="0.1"/>'
        )
    for p in patches(terrain, spec):
        s = SURFACES[p.surface]
        nrow, ncol = p.r1 - p.r0 + 1, p.c1 - p.c0 + 1
        hx, hy = (ncol - 1) * terrain.cell_m / 2, (nrow - 1) * terrain.cell_m / 2
        cx = terrain.x0 + (p.c0 + p.c1) / 2 * terrain.cell_m
        cy = terrain.y0 + (p.r0 + p.r1) / 2 * terrain.cell_m
        assets.append(f'    <hfield name="{p.name}" nrow="{nrow}" ncol="{ncol}" size="{hx} {hy} {span} 0.1"/>')
        bodies.append(
            f'    <geom name="{p.name}" type="hfield" hfield="{p.name}" pos="{cx} {cy} {zmin}" '
            f'material="surf_{p.surface}" {contact_attrs(s.friction)}/>'
        )
    for name, surface, centre, half, quat in structures(terrain, spec):
        bodies.append(
            f'    <geom name="{name}" type="box" pos="{_vec(centre)}" size="{_vec(half)}" quat="{_vec(quat)}" '
            f'material="surf_{surface}" {contact_attrs(SURFACES[surface].friction)}/>'
        )
    return assets, bodies


def _vec(v: tuple) -> str:
    return " ".join(f"{c:.6g}" for c in v)


def contact_attrs(friction: float) -> str:
    """Ground contact, the same for every surface but its sliding friction.

    MuJoCo combines two geoms' frictions by taking the larger and their contact softness by `solmix` weights. The
    simulator gives the feet a negligible friction (`SimAdapter`), so the surface's applies, and the ground a large
    `solmix`, so its contact stiffness applies: a firm contact, a few millimetres of give. The feet's own, much
    softer contact lets them sink below a height field's surface and get caught between cells (ADR-0015).
    """
    return f'friction="{friction} 0.005 0.0001" solmix="10000" solimp="0.9 0.95 0.003" condim="3"'


def fill_heights(model: Any, terrain: TerrainMap, spec: TerrainSpec) -> None:
    """Writes the heights into the compiled model's height fields (MuJoCo stores them normalised to 0..1)."""
    import mujoco

    zmin, zmax = float(terrain.ground.min()), float(terrain.ground.max())
    span = max(zmax - zmin, 1e-3)
    norm = (terrain.ground - zmin) / span
    for p in patches(terrain, spec):
        hid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_HFIELD, p.name)
        adr, n = model.hfield_adr[hid], model.hfield_nrow[hid] * model.hfield_ncol[hid]
        model.hfield_data[adr : adr + n] = norm[p.r0 : p.r1 + 1, p.c0 : p.c1 + 1].ravel()


def _rgb(c: tuple[float, float, float]) -> str:
    return " ".join(f"{v:.3f}" for v in c)


# --- episode copy for the viewer ---------------------------------------------------------------------------------
def viewer_copy(terrain: TerrainMap, obstacle_bases: list[float]) -> dict[str, Any]:
    """`terrain.json`: a coarser grid (heights in mm, surface ids) and the colours, so the viewer draws the same
    ground without knowing the surface table. `obstacles_z` is where each scene obstacle stands."""
    stride = max(1, round(VIEWER_CELL_M / terrain.cell_m))
    h = terrain.heights[::stride, ::stride]
    lab = terrain.labels[::stride, ::stride]
    mm = np.clip(np.round(h * 1000), -32768, 32767).astype("<i2")
    return {
        "format": 1,
        "x0": terrain.x0,
        "y0": terrain.y0,
        "cell_m": round(terrain.cell_m * stride, 6),
        "rows": int(h.shape[0]),
        "cols": int(h.shape[1]),
        "heights_mm": base64.b64encode(mm.tobytes()).decode("ascii"),
        "surface_ids": base64.b64encode(lab.astype(np.uint8).tobytes()).decode("ascii"),
        "surfaces": [
            {"name": n, "rgb": list(SURFACES[n].rgb), "speck": list(SURFACES[n].speck)} for n in terrain.surfaces
        ],
        "obstacles_z": [round(z, 4) for z in obstacle_bases],
    }
