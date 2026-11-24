"""Where the files Spingi ships with are: robot models, walking policies, tag images, sample scenes and plans
(ADR-0027).

In a source checkout they sit next to the package, in `runtime/sim/` and `runtime/plans/`. An installed wheel
carries a copy inside the package, under `spingi/_assets/`, with the same layout. Both are found from this file,
never from the working directory: a folder you run `spingi` in must not supply the robot.

Scenes and plans are also what people write themselves, so a reference to one is resolved in steps: a file that
exists, then `scenes/<name>.yaml` (or `plans/<name>.yaml`) in the working folder, then a sample by its name
(`warehouse_small`) or by the path it has in a checkout (`sim/scenes/warehouse_small.yaml`), so that a command
copied from the documentation runs wherever Spingi is installed.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
_INSTALLED = PACKAGE_DIR / "_assets"
ASSETS_DIR = _INSTALLED if _INSTALLED.is_dir() else PACKAGE_DIR.parent

MODELS_DIR = ASSETS_DIR / "sim" / "models"
POLICIES_DIR = ASSETS_DIR / "sim" / "policies"
MARKERS_DIR = ASSETS_DIR / "sim" / "markers"  # tag36h11_NN.png, ids 0..63
SCENES_DIR = ASSETS_DIR / "sim" / "scenes"
PLANS_DIR = ASSETS_DIR / "plans"

DEFAULT_SCENE = "lab_small"


class UnknownSample(FileNotFoundError):
    """A reference that is neither a file nor the name of a sample."""


class OutsideWorkspace(PermissionError):
    """A reference to a file outside the working folder, where only that folder and the samples may be read."""


def bundled_scenes() -> dict[str, Path]:
    """The sample scenes, by name (`warehouse_small`)."""
    return _samples(SCENES_DIR)


def bundled_plans() -> dict[str, Path]:
    """The sample plans, by name (`demo_inspection_round`). The planner's golden cases are not plans to run."""
    return _samples(PLANS_DIR)


def resolve_scene(ref: Path | str, root: Path | str | None = None, confined: bool = False) -> Path:
    """The scene file `ref` stands for: a path, a scene of the working folder's `scenes/`, or a sample.
    `confined`: the file must be inside the working folder or be a sample, whatever links it goes through."""
    return _confined(_resolve(ref, root, "scenes", "sim/scenes", bundled_scenes(), "scene"), root, ref, confined)


def resolve_plan(ref: Path | str, root: Path | str | None = None, confined: bool = False) -> Path:
    """The plan file `ref` stands for: a path, a plan of the working folder's `plans/`, or a sample."""
    return _confined(_resolve(ref, root, "plans", "plans", bundled_plans(), "plan"), root, ref, confined)


def _confined(path: Path, root: Path | str | None, ref: Path | str, confined: bool) -> Path:
    if confined:
        real = path.resolve()
        base = (Path(root) if root is not None else Path.cwd()).resolve()
        if not (real.is_relative_to(base) or real.is_relative_to(ASSETS_DIR.resolve())):
            raise OutsideWorkspace(f"'{ref}' is outside the working folder {base}")
    return path


def leading_comment(path: Path) -> str:
    """What a sample says about itself: the comment lines at the top of its file, as one line."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    return " ".join(part for part in lines if part)


def summary(path: Path) -> str:
    """The first sentence of a sample's leading comment: one line for a list."""
    first, _, _ = leading_comment(path).partition(". ")
    return first if not first or first.endswith(".") else f"{first}."


def _samples(folder: Path) -> dict[str, Path]:
    return {p.stem: p for p in sorted(folder.glob("*.yaml"))}


def _resolve(
    ref: Path | str, root: Path | str | None, folder: str, checkout: str, samples: dict[str, Path], what: str
) -> Path:
    """`root` is the working folder (default: the current one). Without it the answer is as short as a person
    would type it; with it, a full path."""
    base = Path(root) if root is not None else Path.cwd()
    given = Path(ref)
    direct = given if given.is_absolute() else base / given
    if direct.is_file():
        return given if root is None else direct
    name = str(ref)
    local = base / folder / f"{name}.yaml"
    if local.is_file():
        return _short(local, base) if root is None else local
    if given.suffix == ".yaml" and given.parent.as_posix() == checkout:
        name = given.stem  # the path a sample has in a checkout
    if name in samples:
        return _short(samples[name], base) if root is None else samples[name]
    known = ", ".join(sorted(samples))
    raise UnknownSample(f"no {what} '{ref}': not a file, not in {folder}/, and not one of the samples ({known})")


def _short(path: Path, base: Path) -> Path:
    """Relative to the working folder when the file is inside it, as a person would have typed it."""
    try:
        return Path(os.path.relpath(path, base)) if path.is_relative_to(base.resolve()) else path
    except ValueError:  # another drive on Windows
        return path
