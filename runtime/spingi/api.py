"""The public Python surface of Spingi (ADR-0029).

What a program needs to use Spingi without knowing how it is built: which robots and skills there are, what a scene
holds, whether a scene and a plan are right, running a plan, measuring it, reading what happened. The command line
and the MCP server are two skins over these functions.

    from spingi import api

    check = api.check_plan("yard_patrol", "yard_outdoor", robot="go2")
    report = api.run("yard_patrol", "yard_outdoor", robot="go2", mode="physics")
    print(report.status, report.summary.timeline[-1], report.archive)

A scene or a plan is named as on the command line: a file, a file of the working folder's `scenes/` or `plans/`, or a
sample's name. `root` is that working folder, the current one by default. A plan can also be given as a `TaskPlan`,
a mapping, or text through `parse_plan`. Results are pydantic models: `.model_dump()` gives plain data.

This module, the episode format and the YAML of plans and scenes are the stable interface. The rest of the package is
internal and may change between versions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from spingi import assets
from spingi.assets import OutsideWorkspace, UnknownSample
from spingi.bench import bench as _bench
from spingi.console.terminal import line as _console_line
from spingi.core.events import Event, EventLog
from spingi.core.human import ScriptedHuman
from spingi.core.plan import PlanError, TaskPlan, load_plan, parse_plan, validate_plan
from spingi.core.types import NAME_PATTERN
from spingi.episode import read_manifest
from spingi.metrics import BenchReport, RunMetrics, run_metrics
from spingi.planner.schema import plan_schema as _plan_schema
from spingi.robots import DEFAULT_ROBOT, PROFILES, RobotProfile, get_profile
from spingi.scene_check import SceneWarning
from spingi.scene_check import check_plan as _plan_warnings
from spingi.scene_check import check_scene as _scene_warnings
from spingi.scenes import SceneError, SceneFile, describe_world, load_scene, parse_scene, read_scene_yaml
from spingi.scenes import json_schema as scene_schema
from spingi.session import MODES, Mode, SessionConfig, new_run_id, run_session
from spingi.skills import default_registry
from spingi.skills import summary as _skill_summary

__all__ = [
    "DEFAULT_ROBOT",
    "DEFAULT_SCENE",
    "VIEWER_URL",
    "BenchReport",
    "EpisodeSummary",
    "EventPage",
    "Mode",
    "OutsideWorkspace",
    "PlanCheck",
    "PlanError",
    "RobotSummary",
    "RunReport",
    "Sample",
    "SceneCheck",
    "SceneDescription",
    "SceneError",
    "SceneMap",
    "SceneWarning",
    "SkillSummary",
    "StepSummary",
    "TaskPlan",
    "UnknownSample",
    "abench",
    "arun",
    "bench",
    "check_plan",
    "check_scene",
    "describe_scene",
    "episode_summary",
    "episodes",
    "events",
    "frame",
    "guide",
    "guides",
    "locate_plan",
    "locate_scene",
    "parse_plan",
    "plan_schema",
    "plans",
    "read_plan",
    "read_scene",
    "robots",
    "run",
    "save_scene",
    "scene_map",
    "scene_schema",
    "scenes",
    "skills",
]

DEFAULT_SCENE = assets.DEFAULT_SCENE
VIEWER_URL = "https://spingi-viewer.netlify.app"
OnFailure = Literal["abort", "skip", "retry"]
PlanSource = Path | str | dict[str, Any] | TaskPlan
RUN_ID_PATTERN = r"^r-[A-Za-z0-9-]{1,80}$"
_RUN_ID = re.compile(RUN_ID_PATTERN)
_GUIDES_DIR = Path(__file__).resolve().parent / "guides"
_FRAME_TYPES = {".png", ".jpg", ".jpeg"}


# --- what there is -----------------------------------------------------------------------------------------------
class RobotSummary(BaseModel):
    alias: str = Field(description="the short name, as on the command line: g1, go2")
    name: str = Field(description="the name written in episodes: unitree_g1")
    kind: str
    label: str
    capabilities: list[str]


class SkillSummary(BaseModel):
    name: str
    summary: str = Field(description="name(params): what it does")
    requires: list[str] = Field(description="robot capabilities the skill needs")
    default_deadline_s: float
    parameters: dict[str, Any] = Field(description="JSON schema of the skill's parameters")


class Sample(BaseModel):
    name: str
    summary: str
    source: Literal["sample", "folder"] = Field(description="shipped with Spingi, or a file of the working folder")
    path: str


def robots() -> list[RobotSummary]:
    return [_robot_summary(p) for p in PROFILES.values()]


def skills(robot: str | None = None) -> list[SkillSummary]:
    """The whitelisted skills; with `robot`, only the ones that robot can run."""
    registry = default_registry()
    if robot is not None:
        registry = registry.subset(_profile(robot).capabilities)
    return [
        SkillSummary(
            name=name,
            summary=_skill_summary(registry.get(name)),
            requires=sorted(registry.get(name).requires),
            default_deadline_s=registry.get(name).default_deadline_s,
            parameters=registry.get(name).Params.model_json_schema(),
        )
        for name in registry.names()
    ]


def plan_schema(robot: str = DEFAULT_ROBOT) -> dict[str, Any]:
    """JSON schema of a plan for this robot: only its skills, each with its parameters."""
    return _plan_schema(default_registry().subset(_profile(robot).capabilities))


def scenes(root: Path | str | None = None) -> list[Sample]:
    """The sample scenes and the scenes of the working folder's `scenes/`, which come first when names clash."""
    return _listed(assets.bundled_scenes(), root, "scenes")


def plans(root: Path | str | None = None) -> list[Sample]:
    return _listed(assets.bundled_plans(), root, "plans")


def locate_scene(scene: Path | str, root: Path | str | None = None, confined: bool = False) -> Path:
    """The file a scene reference stands for. `confined` refuses, with `OutsideWorkspace`, a file that is neither
    inside the working folder nor a sample: for a caller that passes on references it did not write."""
    return assets.resolve_scene(scene, root, confined)


def locate_plan(plan: Path | str, root: Path | str | None = None, confined: bool = False) -> Path:
    return assets.resolve_plan(plan, root, confined)


def guides() -> list[str]:
    """The topics of the short guides written for whoever writes scenes and plans, a person or an agent."""
    return sorted(p.stem for p in _GUIDES_DIR.glob("*.md"))


def guide(topic: str) -> str:
    if topic not in guides():
        raise ValueError(f"no guide '{topic}'; known: {', '.join(guides())}")
    return (_GUIDES_DIR / f"{topic}.md").read_text(encoding="utf-8")


def read_scene(scene: Path | str, root: Path | str | None = None) -> str:
    """The YAML text of a scene, to read it or to start another from it."""
    return assets.resolve_scene(scene, root).read_text(encoding="utf-8")


def read_plan(plan: Path | str, root: Path | str | None = None) -> str:
    return assets.resolve_plan(plan, root).read_text(encoding="utf-8")


# --- scenes ------------------------------------------------------------------------------------------------------
class SceneDescription(BaseModel):
    """What a scene holds, as data and as the text the planner is given."""

    name: str
    start: dict[str, float] = Field(description="where the robot starts: x, y, yaw")
    locations: dict[str, dict[str, float]] = Field(description="name -> x, y, yaw, tolerance_m")
    objects: dict[str, dict[str, Any]] = Field(description="id -> cls, marker_id, x, y, z when known")
    obstacles: list[dict[str, float]] = Field(description="boxes: index, x, y (centre), w, d, h")
    landmarks: list[dict[str, Any]]
    routes: dict[str, list[str]]
    safety: dict[str, Any]
    terrain: dict[str, Any] | None = Field(description="the ground outdoors, summarised; None indoors")
    text: str


class SceneCheck(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list, description="why the file is not a scene, one line each")
    warnings: list[SceneWarning] = Field(default_factory=list, description="what a robot would meet in it")
    description: SceneDescription | None = None


def describe_scene(scene: Path | str = DEFAULT_SCENE, root: Path | str | None = None) -> SceneDescription:
    path = assets.resolve_scene(scene, root)
    return _describe(load_scene(path), path.stem)


def check_scene(
    scene: Path | str | None = None, *, text: str | None = None, root: Path | str | None = None
) -> SceneCheck:
    """Whether a file, or YAML `text`, is a scene, and what its geometry would do to a robot (ADR-0028)."""
    try:
        model, name = _scene(scene, text, root)
    except SceneError as exc:
        return SceneCheck(valid=False, errors=exc.errors)
    return SceneCheck(valid=True, warnings=_scene_warnings(model), description=_describe(model, name))


class SceneMap(BaseModel):
    path: str = Field(description="the PNG")
    warnings: list[SceneWarning] = Field(description="what is marked in red on it, in the order of the numbers")


def scene_map(
    scene: Path | str | None = None,
    *,
    text: str | None = None,
    plan: PlanSource | None = None,
    out: Path | str | None = None,
    root: Path | str | None = None,
) -> SceneMap:
    """Draws a scene from above as a PNG, with the warnings of the scene and, if given, of a plan in it. A file or
    a text that is not a scene raises `SceneError`, a plan that is not a plan `PlanError`."""
    from spingi.scene_map import write_map

    model, name = _scene(scene, text, root)
    warnings = _scene_warnings(model)
    if plan is not None:
        warnings = [*warnings, *_plan_warnings(_plan(plan, root), model)]
    target = Path(out) if out is not None else _base(root) / f"{name}-map.png"
    return SceneMap(path=str(write_map(model, target, warnings, title=name)), warnings=warnings)


def save_scene(name: str, text: str, root: Path | str | None = None, overwrite: bool = False) -> Path:
    """Writes a scene to `scenes/<name>.yaml` of the working folder, once it is a scene. It is then found by its
    name. Raises `SceneError` for a text that is not a scene and `FileExistsError` unless `overwrite`."""
    if not re.match(NAME_PATTERN, name):
        raise ValueError(f"invalid scene name {name!r}: use letters, digits, '_' or '-', at most 64 characters")
    parse_scene(read_scene_yaml(text, name), name)
    path = _base(root) / "scenes" / f"{name}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists: pass overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


# --- plans -------------------------------------------------------------------------------------------------------
class PlanCheck(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list, description="why the plan cannot run, one line each")
    warnings: list[SceneWarning] = Field(default_factory=list, description="what the plan would meet in its scene")
    plan_id: str | None = None
    steps: int = 0


def check_plan(
    plan: PlanSource, scene: Path | str = DEFAULT_SCENE, robot: str = DEFAULT_ROBOT, root: Path | str | None = None
) -> PlanCheck:
    """The check a run makes before the robot moves, with the same messages, and what the plan would meet in the
    scene. A scene that is not a scene raises `SceneError`: check the scene first."""
    model = load_scene(assets.resolve_scene(scene, root))
    try:
        task = _plan(plan, root)
    except PlanError as exc:
        return PlanCheck(valid=False, errors=[str(exc)])
    errors = validate_plan(task, default_registry(), _profile(robot).capabilities, model.locations)
    warnings = [] if errors else _plan_warnings(task, model)
    return PlanCheck(valid=not errors, errors=errors, warnings=warnings, plan_id=task.id, steps=len(task.steps))


# --- running -----------------------------------------------------------------------------------------------------
class StepSummary(BaseModel):
    index: int
    skill: str
    params: dict[str, Any]
    outcome: str = Field(description="success, skipped, or how the last attempt ended")
    attempts: int
    reason: str = ""
    sim_t_start: float | None = None
    sim_t_end: float | None = None


class EpisodeSummary(BaseModel):
    """What happened in a run, from its episode: the outcome, each step, the console's lines, the numbers."""

    run_id: str
    plan_id: str
    robot: str
    adapter: str
    status: str = Field(description="success | aborted | invalid_plan | deadline | estop | error")
    steps_completed: int
    steps_total: int
    sim_time_s: float | None
    errors: list[str] = Field(default_factory=list, description="why the plan was refused, if it was")
    steps: list[StepSummary]
    metrics: RunMetrics
    timeline: list[str]
    frames: list[str] = Field(description="head-camera frames the robot analysed, relative to the episode folder")
    files: list[str]
    episode_dir: str
    archive: str | None = Field(default=None, description="the episode as a .zip, for the viewer")
    viewer: str = Field(description="how to watch the run")


class RunReport(BaseModel):
    run_id: str
    status: str
    ok: bool
    steps_completed: int
    steps_total: int
    sim_time_s: float | None
    episode_dir: str
    archive: str | None
    video: str | None
    camera_video: str | None
    summary: EpisodeSummary


async def arun(
    plan: PlanSource,
    scene: Path | str = DEFAULT_SCENE,
    *,
    robot: str = DEFAULT_ROBOT,
    mode: Mode = "fake",
    seed: int = 0,
    noise: float = 0.0,
    sigma: float = 0.0,
    on_failure: OnFailure = "abort",
    camera_video: bool = False,
    video: bool = False,
    zip_episode: bool = True,
    realtime: bool = False,
    runs_dir: Path | str | None = None,
    root: Path | str | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> RunReport:
    """Runs a plan once and leaves an episode.

    `mode`: `fake` (in-memory robot, instant), `kinematic` (MuJoCo, the base glides: fast and repeatable), `physics`
    (the robot walks and can be blocked or fall), `realistic` (walking, with drifting odometry, a noisy camera and
    tags read from frames). A step that needs a person gets `on_failure` as the answer, every time. `on_event` is
    called with each event as it happens. A plan or a scene that is not one raises `PlanError` or `SceneError`
    before anything starts; a plan that is wrong for the scene or the robot ends the run as `invalid_plan`.
    Cancelling the task stops the robot, ends the run as aborted and still writes the episode.
    """
    cfg = _session(plan, scene, robot, mode, seed, noise, sigma, runs_dir, root)
    cfg.record_camera, cfg.record_video, cfg.zip_episode, cfg.realtime = camera_video, video, zip_episode, realtime
    cfg.__post_init__()  # the settings above may contradict the mode: refused here, before anything is loaded
    run_id = new_run_id()
    log = EventLog(run_id=run_id, path=cfg.runs_dir / run_id / "events.jsonl")
    if on_event is not None:
        log.subscribe(on_event)
    result = await run_session(cfg, ScriptedHuman(default=on_failure), log=log, stop_button=False)
    return RunReport(
        run_id=result.run_id,
        status=result.status,
        ok=result.ok,
        steps_completed=result.steps_completed,
        steps_total=result.steps_total,
        sim_time_s=None if result.sim_time_s is None else round(result.sim_time_s, 3),
        episode_dir=str(result.run_dir),
        archive=str(result.archive) if result.archive else None,
        video=str(result.video) if result.video else None,
        camera_video=str(result.camera_video) if result.camera_video else None,
        summary=_summary(result.run_dir),
    )


def run(plan: PlanSource, scene: Path | str = DEFAULT_SCENE, **options: Any) -> RunReport:
    """`arun` for programs without an event loop."""
    return asyncio.run(arun(plan, scene, **options))


async def abench(
    plan: PlanSource,
    scene: Path | str = DEFAULT_SCENE,
    *,
    runs: int = 20,
    robot: str = DEFAULT_ROBOT,
    mode: Mode = "kinematic",
    seed: int = 0,
    noise: float = 0.0,
    sigma: float = 0.0,
    on_failure: OnFailure = "abort",
    keep_failed: bool = False,
    runs_dir: Path | str | None = None,
    root: Path | str | None = None,
    on_run: Callable[[int, int, RunMetrics], None] | None = None,
) -> BenchReport:
    """Runs a plan `runs` times, each with the next seed, and checks the sim-to-real gate: `report.passed`.
    The report and one line per run go to `runs/bench-<id>/`. `on_run(done, total, metrics)` is called per run."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    cfg = _session(plan, scene, robot, mode, seed, noise, sigma, runs_dir, root)
    out_dir = cfg.runs_dir / f"bench-{new_run_id()[2:]}"
    report, _ = await _bench(
        cfg, runs, on_failure=on_failure, out_dir=out_dir, keep_failed_episodes=keep_failed, progress=on_run
    )
    return report


def bench(plan: PlanSource, scene: Path | str = DEFAULT_SCENE, **options: Any) -> BenchReport:
    return asyncio.run(abench(plan, scene, **options))


# --- what happened -----------------------------------------------------------------------------------------------
class EventPage(BaseModel):
    run_id: str
    total: int = Field(description="events of these kinds in the episode")
    offset: int
    events: list[dict[str, Any]]


def episodes(runs_dir: Path | str | None = None, root: Path | str | None = None, limit: int = 20) -> list[str]:
    """The run ids of the episodes in the runs folder, newest first."""
    folder = _runs_dir(runs_dir, root)
    if not folder.is_dir():
        return []
    found = [(p / "manifest.json").stat().st_mtime_ns for p in folder.iterdir() if (p / "manifest.json").is_file()]
    names = [p.name for p in folder.iterdir() if (p / "manifest.json").is_file()]
    newest_first = sorted(zip(found, names, strict=True), reverse=True)
    return [name for _, name in newest_first][:limit]


def episode_summary(
    run: Path | str = "latest", runs_dir: Path | str | None = None, root: Path | str | None = None
) -> EpisodeSummary:
    """`run`: a run id, `latest`, or the path of an episode folder."""
    return _summary(_episode_dir(run, runs_dir, root))


def events(
    run: Path | str = "latest",
    *,
    kinds: list[str] | None = None,
    offset: int = 0,
    limit: int = 200,
    runs_dir: Path | str | None = None,
    root: Path | str | None = None,
) -> EventPage:
    """A page of an episode's events, oldest first. `kinds` keeps the kinds that are, or start with, one of them:
    `safety` keeps `safety.estop`."""
    folder = _episode_dir(run, runs_dir, root)
    log = EventLog.read(folder / "events.jsonl")
    if kinds:
        log = [e for e in log if any(e.kind == k or e.kind.startswith(f"{k}.") for k in kinds)]
    page = log[max(offset, 0) : max(offset, 0) + max(limit, 0)]
    return EventPage(
        run_id=folder.name, total=len(log), offset=max(offset, 0), events=[json.loads(e.to_jsonl()) for e in page]
    )


def frame(run: Path | str, ref: str, runs_dir: Path | str | None = None, root: Path | str | None = None) -> Path:
    """The file of a head-camera frame, by the `frame_ref` an event carries. An episode may come from a stranger:
    only an image inside the episode's `frames/` folder is returned."""
    folder = _episode_dir(run, runs_dir, root).resolve()
    path = (folder / ref).resolve()
    if not path.is_relative_to(folder / "frames") or path.suffix.lower() not in _FRAME_TYPES or not path.is_file():
        raise ValueError(f"no frame {ref!r} in episode {folder.name}")
    return path


# --- inside ------------------------------------------------------------------------------------------------------
def _base(root: Path | str | None) -> Path:
    return Path(root) if root is not None else Path()


def _runs_dir(runs_dir: Path | str | None, root: Path | str | None) -> Path:
    return Path(runs_dir) if runs_dir is not None else _base(root) / "runs"


def _profile(robot: str) -> RobotProfile:
    try:
        return get_profile(robot)
    except KeyError as exc:
        raise ValueError(str(exc.args[0])) from exc


def _robot_summary(profile: RobotProfile) -> RobotSummary:
    return RobotSummary(
        alias=profile.alias,
        name=profile.name,
        kind=profile.kind,
        label=profile.label,
        capabilities=sorted(profile.capabilities),
    )


def _listed(samples: dict[str, Path], root: Path | str | None, folder: str) -> list[Sample]:
    mine = {p.stem: p for p in sorted((_base(root) / folder).glob("*.yaml"))}
    listed = [Sample(name=n, summary=assets.summary(p), source="folder", path=str(p)) for n, p in mine.items()]
    listed += [
        Sample(name=n, summary=assets.summary(p), source="sample", path=str(p))
        for n, p in samples.items()
        if n not in mine
    ]
    return listed


def _scene(scene: Path | str | None, text: str | None, root: Path | str | None) -> tuple[SceneFile, str]:
    """The scene given as a reference or as text, and a name for it."""
    if (scene is None) == (text is None):
        raise ValueError("give a scene or its text, one of the two")
    if text is not None:
        return parse_scene(read_scene_yaml(text)), "scene"
    path = assets.resolve_scene(scene, root)
    return load_scene(path), path.stem


def _plan(plan: PlanSource, root: Path | str | None) -> TaskPlan:
    if isinstance(plan, TaskPlan):
        return plan
    if isinstance(plan, dict):
        return parse_plan(plan)
    return load_plan(assets.resolve_plan(plan, root))


def _plan_file(plan: PlanSource, root: Path | str | None, runs_dir: Path) -> Path:
    """The file of a plan. One given as data is written under the runs folder, named by its id and its content, so
    that the episode can keep a copy and the same plan is the same file."""
    if isinstance(plan, (str, Path)):
        path = assets.resolve_plan(plan, root)
        load_plan(path)
        return path
    task = _plan(plan, root)
    text = yaml.safe_dump(task.model_dump(exclude_defaults=True), sort_keys=False, allow_unicode=True)
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    path = runs_dir / "plans" / f"{task.id}-{digest}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _session(
    plan: PlanSource,
    scene: Path | str,
    robot: str,
    mode: str,
    seed: int,
    noise: float,
    sigma: float,
    runs_dir: Path | str | None,
    root: Path | str | None,
) -> SessionConfig:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; known: {', '.join(MODES)}")
    runs = _runs_dir(runs_dir, root)
    scene_path = assets.resolve_scene(scene, root)
    load_scene(scene_path)  # a file that is not a scene is refused before a run folder is made
    return SessionConfig(
        plan=_plan_file(plan, root, runs),
        scene=scene_path,
        robot=_profile(robot).alias,
        runs_dir=runs,
        seed=seed,
        perception_noise=noise,
        position_sigma_m=sigma,
        **MODES[mode],
    )


def _describe(scene: SceneFile, name: str) -> SceneDescription:
    world = scene.world()
    terrain = scene.terrain
    return SceneDescription(
        name=name,
        start=scene.robot.pose.model_dump(),
        locations={n: {**loc.pose.model_dump(), "tolerance_m": loc.tolerance_m} for n, loc in scene.locations.items()},
        objects={
            oid: {"cls": o.cls, "marker_id": o.marker_id, **(o.pose.model_dump() if o.pose else {})}
            for oid, o in scene.objects.items()
        },
        obstacles=[
            {"index": i, "x": o.x, "y": o.y, "w": o.w, "d": o.d, "h": o.h} for i, o in enumerate(scene.obstacles)
        ],
        landmarks=[lm.model_dump() for lm in scene.landmarks],
        routes=dict(scene.routes),
        safety=scene.safety.model_dump(),
        terrain=None
        if terrain is None
        else {
            "extent": terrain.extent.model_dump(),
            "surface": terrain.surface,
            "regions": [r.model_dump(exclude_none=True) for r in terrain.regions],
            "ramps": [r.model_dump() for r in terrain.ramps],
            "raised": [r.model_dump() for r in terrain.raised],
        },
        text=describe_world(world, dict(scene.routes)),
    )


def _episode_dir(run: Path | str, runs_dir: Path | str | None, root: Path | str | None) -> Path:
    if isinstance(run, Path) or (isinstance(run, str) and Path(run).is_dir()):
        folder = Path(run)
    elif run == "latest":
        known = episodes(runs_dir, root, limit=1)
        if not known:
            raise FileNotFoundError(f"no episode in {_runs_dir(runs_dir, root)}")
        folder = _runs_dir(runs_dir, root) / known[0]
    elif _RUN_ID.match(str(run)):
        folder = _runs_dir(runs_dir, root) / str(run)
    else:
        raise FileNotFoundError(f"no episode {run!r}: give a run id, 'latest' or an episode folder")
    if not (folder / "manifest.json").is_file():
        raise FileNotFoundError(f"no episode in {folder}")
    return folder


def _summary(folder: Path) -> EpisodeSummary:
    manifest = read_manifest(folder)
    log = EventLog.read(folder / "events.jsonl")
    archive = folder.with_suffix(".zip")
    refs = dict.fromkeys(str(e.data["frame_ref"]) for e in log if e.data.get("frame_ref"))
    invalid = next((e for e in log if e.kind == "plan.invalid"), None)
    watch = f"open {VIEWER_URL} and drop {archive} on the page" if archive.is_file() else (
        f"zip the folder {folder} and drop it on {VIEWER_URL}"
    )  # fmt: skip
    return EpisodeSummary(
        run_id=manifest.run_id,
        plan_id=manifest.plan_id,
        robot=manifest.robot.model,
        adapter=manifest.robot.adapter,
        status=manifest.status,
        steps_completed=manifest.steps_completed,
        steps_total=manifest.steps_total,
        sim_time_s=manifest.sim_time_s,
        errors=[str(e) for e in (invalid.data.get("errors") or [])] if invalid else [],
        steps=_steps(log),
        metrics=run_metrics(log, manifest.steps_total),
        timeline=[text for text in map(_console_line, log) if text is not None],
        frames=[ref for ref in refs if (folder / ref).is_file()],
        files=manifest.files,
        episode_dir=str(folder),
        archive=str(archive) if archive.is_file() else None,
        viewer=watch,
    )


def _steps(log: list[Event]) -> list[StepSummary]:
    """Each step as it went: how many attempts, how the last one ended and why."""
    steps: list[StepSummary] = []
    for event in log:
        d = event.data
        if event.kind == "step.start":
            steps.append(
                StepSummary(
                    index=d.get("index", len(steps)),
                    skill=str(d.get("skill")),
                    params=d.get("params") or {},
                    outcome="unfinished",
                    attempts=0,
                    sim_t_start=d.get("sim_t"),
                )
            )
        elif steps and event.kind == "skill.end":
            step = steps[-1]
            step.attempts += 1
            step.outcome, step.reason, step.sim_t_end = (
                str(d.get("outcome")),
                str(d.get("reason") or ""),
                d.get("sim_t"),
            )
        elif steps and event.kind == "skill.precondition_failed":
            steps[-1].outcome, steps[-1].reason = "precondition_failed", str(d.get("reason") or "")
        elif steps and event.kind == "step.end":
            steps[-1].outcome, steps[-1].sim_t_end = str(d.get("outcome")), d.get("sim_t")
    return steps
