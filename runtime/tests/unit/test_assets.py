"""Where the shipped files are found, and what a reference to a scene or a plan stands for (ADR-0027)."""

import tomllib
from pathlib import Path

import pytest

from spingi import assets
from spingi.assets import (
    ASSETS_DIR,
    MARKERS_DIR,
    MODELS_DIR,
    PLANS_DIR,
    POLICIES_DIR,
    SCENES_DIR,
    UnknownSample,
    bundled_plans,
    bundled_scenes,
    leading_comment,
    resolve_plan,
    resolve_scene,
    summary,
)

RUNTIME = Path(__file__).resolve().parents[2]


def test_a_checkout_keeps_the_assets_next_to_the_package():
    assert ASSETS_DIR == RUNTIME
    assert {p.name for p in MODELS_DIR.iterdir() if p.is_dir()} == {"unitree_g1", "unitree_go2"}
    assert (POLICIES_DIR / "go2_himloco" / "policy.npz").is_file()
    assert len(list(MARKERS_DIR.glob("tag36h11_*.png"))) == 64


def test_the_wheel_carries_every_asset_folder_where_the_package_looks_for_it():
    """`spingi/_assets` mirrors the checkout: a folder missing here is a robot that cannot be simulated once
    installed."""
    build = tomllib.loads((RUNTIME / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    for folder in (MODELS_DIR, POLICIES_DIR, MARKERS_DIR, SCENES_DIR, PLANS_DIR):
        source = folder.relative_to(RUNTIME).as_posix()
        assert build["force-include"].get(source) == f"spingi/_assets/{source}", source


def test_an_installed_package_reads_its_own_copy(tmp_path):
    package = tmp_path / "spingi"
    (package / "_assets" / "sim" / "scenes").mkdir(parents=True)
    source = (RUNTIME / "spingi" / "assets.py").read_text()
    module: dict = {"__file__": str(package / "assets.py"), "__name__": "installed_assets"}
    exec(compile(source, module["__file__"], "exec"), module)  # noqa: S102 - our own module, as if installed there
    assert module["ASSETS_DIR"] == (package / "_assets").resolve()
    assert module["SCENES_DIR"] == (package / "_assets" / "sim" / "scenes").resolve()


def test_samples_are_known_by_name():
    assert set(bundled_scenes()) >= {"lab_small", "warehouse_small", "yard_outdoor", "test_track"}
    assert set(bundled_plans()) == {"demo_inspection_round", "demo_material_runner", "test_track", "yard_patrol"}
    assert "planner_cases" not in bundled_plans()  # the planner's golden cases are not plans to run


def test_a_name_and_a_checkout_path_stand_for_the_same_sample_from_any_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    by_name = resolve_scene("warehouse_small")
    assert by_name == SCENES_DIR / "warehouse_small.yaml" and by_name.is_file()
    assert resolve_scene("sim/scenes/warehouse_small.yaml") == by_name
    assert resolve_plan("yard_patrol") == resolve_plan("plans/yard_patrol.yaml") == PLANS_DIR / "yard_patrol.yaml"


def test_inside_the_checkout_a_sample_keeps_the_path_a_person_types(monkeypatch):
    monkeypatch.chdir(RUNTIME)
    assert resolve_scene("lab_small") == Path("sim/scenes/lab_small.yaml")
    assert resolve_scene("sim/scenes/lab_small.yaml") == Path("sim/scenes/lab_small.yaml")


def test_a_file_wins_over_a_sample_and_so_does_the_working_folders_own_scene(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scenes").mkdir()
    mine = tmp_path / "scenes" / "lab_small.yaml"
    mine.write_text("locations: {}\n")
    assert resolve_scene("lab_small") == Path("scenes/lab_small.yaml")
    assert resolve_scene("scenes/lab_small.yaml") == Path("scenes/lab_small.yaml")
    assert resolve_scene(mine) == mine


def test_a_working_folder_can_be_given_and_the_answer_is_then_a_full_path(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "round.yaml").write_text("id: round\nsteps: []\n")
    assert resolve_plan("round", root=tmp_path) == tmp_path / "plans" / "round.yaml"
    assert resolve_plan("plans/round.yaml", root=tmp_path) == tmp_path / "plans" / "round.yaml"
    assert resolve_plan("yard_patrol", root=tmp_path) == PLANS_DIR / "yard_patrol.yaml"


def test_an_unknown_reference_names_the_samples():
    with pytest.raises(UnknownSample, match=r"no scene 'kitchen'.*lab_small.*yard_outdoor"):
        resolve_scene("kitchen")
    with pytest.raises(UnknownSample, match=r"no plan 'plans/missing\.yaml'"):
        resolve_plan("plans/missing.yaml")


def test_a_sample_describes_itself_in_its_leading_comment():
    scene = bundled_scenes()["warehouse_small"]
    assert leading_comment(scene).startswith("Small warehouse: two shelving racks form an aisle;")
    assert summary(scene) == (
        "Small warehouse: two shelving racks form an aisle; the box sits on rack A; the workstation is a table."
    )
    for path in (*bundled_scenes().values(), *bundled_plans().values()):
        assert summary(path).endswith("."), f"{path.name} does not say what it is"


def test_a_file_without_a_comment_has_no_summary(tmp_path):
    bare = tmp_path / "bare.yaml"
    bare.write_text("locations: {}\n# not at the top\n")
    assert leading_comment(bare) == "" and summary(bare) == ""
    assert assets.DEFAULT_SCENE in bundled_scenes()
