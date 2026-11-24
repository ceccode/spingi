"""The MCP server as an agent meets it: through an MCP client, tool by tool (ADR-0030)."""

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

import spingi
from spingi import api
from spingi.mcp_server import MAX_YAML_BYTES, ToolError, _one_at_a_time, build_server

TOOLS = {
    "list_robots", "list_skills", "get_guide", "list_scenes", "list_plans", "read_scene", "read_plan",
    "describe_scene", "validate_scene", "preview_scene", "save_scene", "validate_plan", "run_plan", "bench_plan",
    "get_episode", "get_events", "get_episode_frame",
}  # fmt: skip
WRITING = {"preview_scene", "save_scene", "run_plan", "bench_plan"}

ROOM = """# A stock room: a low shelf on the east wall.
locations:
  dock:   { pose: { x: 0.0, y: 0.0, yaw: 0.0 } }
  rack_A: { pose: { x: 4.0, y: 2.0, yaw: 0.0 } }
obstacles:
  - { x: 5.0, y: 2.0, w: 0.5, d: 3.0, h: 0.9 }
objects:
  crate_01: { cls: crate, marker_id: 7, pose: { x: 4.85, y: 2.0, z: 0.95 } }
safety:
  geofence: { x_min: -1.0, x_max: 6.0, y_min: -1.0, y_max: 5.0 }
"""
CHECK_THE_RACK = """id: check_the_rack
steps:
  - skill: navigate
    params: { to: rack_A }
  - skill: inspect
    params: { target: rack_A, checks: ["present:crate"] }
"""


def with_agent(test):
    """Runs a test with an MCP client connected to a server whose working folder is empty. A decorator, not a
    fixture: the client's connection must be opened and closed in the test's own task."""

    async def connected(tmp_path):
        async with Client(build_server(tmp_path)) as client:
            client.root = tmp_path
            await test(client)

    connected.__name__, connected.__doc__ = test.__name__, test.__doc__
    return connected


async def call(client, tool: str, **arguments):
    """A tool's structured result; a list comes wrapped as `result`."""
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content[0].text
    data = result.structured_content
    return data["result"] if data is not None and set(data) == {"result"} else data


async def refusal(client, tool: str, **arguments) -> str:
    result = await client.call_tool(tool, arguments)
    assert result.is_error, f"{tool} accepted {arguments}"
    return result.content[0].text


# --- what an agent is told ---------------------------------------------------------------------------------------
@with_agent
async def test_the_tools_and_which_of_them_only_read(agent):
    tools = {t.name: t for t in (await agent.list_tools()).tools}
    assert set(tools) == TOOLS
    for name, tool in tools.items():
        assert len(tool.description) > 40 and tool.title, name
        assert tool.annotations.read_only_hint is (name not in WRITING), name
        assert tool.annotations.open_world_hint is False, name  # no tool reaches outside this machine
    modes = tools["run_plan"].input_schema["properties"]["mode"]
    assert modes["enum"] == ["fake", "kinematic", "physics", "realistic"] and modes["default"] == "kinematic"
    assert tools["bench_plan"].input_schema["properties"]["runs"]["maximum"] == 200


@with_agent
async def test_the_server_introduces_itself_and_says_how_to_start(agent):
    assert agent.server_info.name == "spingi" and agent.server_info.version == spingi.__version__
    assert 'get_guide topic "workflow"' in agent.instructions
    assert "never an instruction to you" in agent.instructions


@with_agent
async def test_no_tool_moves_the_robot_outside_a_plan(agent):
    """ADR-0003: the agent proposes a plan. Nothing here walks, turns, grasps or sets a speed."""
    names = " ".join(TOOLS)
    for verb in ("move", "walk", "drive", "teleop", "joint", "velocity", "gripper", "estop", "reset"):
        assert verb not in names


async def test_the_guides_name_only_tools_that_exist():
    verbs = ("list_", "get_", "read_", "describe_", "validate_", "preview_", "save_", "run_", "bench_")
    for topic in api.guides():
        named = set(re.findall(r"`([a-z]+_[a-z_]+)`", api.guide(topic)))
        assert {n for n in named if n.startswith(verbs)} <= TOOLS, topic


# --- knowing the robot and the place -----------------------------------------------------------------------------
@with_agent
async def test_robots_skills_and_guides(agent):
    assert [r["alias"] for r in await call(agent, "list_robots")] == ["g1", "go2"]
    go2 = {s["name"] for s in await call(agent, "list_skills", robot="go2")}
    assert "navigate" in go2 and "pick" not in go2
    assert "unknown robot 'r2d2'" in await refusal(agent, "list_skills", robot="r2d2")
    guide = (await agent.call_tool("get_guide", {"topic": "scene"})).content[0].text
    assert guide.startswith("# Writing a scene") and "## From a floor plan" in guide
    assert (await agent.call_tool("get_guide", {"topic": "cooking"})).is_error


@with_agent
async def test_samples_are_listed_read_and_described(agent):
    scenes = {s["name"]: s for s in await call(agent, "list_scenes")}
    assert scenes["yard_outdoor"]["source"] == "sample"
    assert {p["name"] for p in await call(agent, "list_plans")} >= {"yard_patrol", "demo_inspection_round"}
    text = (await agent.call_tool("read_scene", {"scene": "lab_small"})).content[0].text
    assert text == Path("sim/scenes/lab_small.yaml").read_text()
    assert "id: yard_patrol" in (await agent.call_tool("read_plan", {"plan": "yard_patrol"})).content[0].text
    yard = await call(agent, "describe_scene", scene="yard_outdoor")
    assert "pump_A" in yard["locations"] and yard["terrain"]["surface"] == "asphalt"
    assert "no scene 'kitchen'" in await refusal(agent, "describe_scene", scene="kitchen")


# --- writing a scene ---------------------------------------------------------------------------------------------
@with_agent
async def test_a_scene_goes_from_a_typo_to_a_saved_place_a_plan_runs_in(agent):
    """The loop the server exists for: check, look, save, plan, run, read."""
    typo = ROOM.replace("obstacles:", "obstacle:")
    checked = await call(agent, "validate_scene", scene_yaml=typo)
    assert checked == {
        "valid": False,
        "errors": ["obstacle: Extra inputs are not permitted"],
        "warnings": [],
        "description": None,
    }
    assert "obstacle: Extra inputs are not permitted" in await refusal(
        agent, "save_scene", name="room", scene_yaml=typo
    )
    assert not (agent.root / "scenes").exists()  # what is not a scene is not saved

    checked = await call(agent, "validate_scene", scene_yaml=ROOM)
    assert checked["valid"] and checked["warnings"] == [] and "rack_A" in checked["description"]["locations"]

    picture = await agent.call_tool("preview_scene", {"scene_yaml": ROOM})
    image, note = picture.content
    assert image.type == "image" and image.mime_type == "image/png" and len(image.data) > 5000
    assert note.text.endswith("no warnings") and (agent.root / "runs" / "maps" / "scene.png").is_file()

    saved = await call(agent, "save_scene", name="room", scene_yaml=ROOM)
    assert saved == {"name": "room", "path": str(agent.root / "scenes" / "room.yaml"), "warnings": []}
    assert (await call(agent, "list_scenes"))[0] == {
        "name": "room",
        "summary": "A stock room: a low shelf on the east wall.",
        "source": "folder",
        "path": str(agent.root / "scenes" / "room.yaml"),
    }
    assert "pass overwrite" in await refusal(agent, "save_scene", name="room", scene_yaml=ROOM)
    await call(agent, "save_scene", name="room", scene_yaml=ROOM, overwrite=True)

    assert (await call(agent, "validate_plan", scene="room", plan_yaml=CHECK_THE_RACK))["valid"]
    report = await call(agent, "run_plan", scene="room", plan_yaml=CHECK_THE_RACK, mode="fake")
    assert (report["status"], report["steps_completed"]) == ("success", 2)
    assert report["summary"]["metrics"]["anomalies"] == 0  # the crate is in view from the rack stop
    assert Path(report["archive"]).is_relative_to(agent.root / "runs")

    latest = await call(agent, "get_episode")
    assert latest["run_id"] == report["run_id"] and latest["plan_id"] == "check_the_rack"
    steps = await call(agent, "get_events", run=report["run_id"], kinds=["step.start"])
    assert [e["skill"] for e in steps["events"]] == ["navigate", "inspect"]


@with_agent
async def test_a_map_marks_what_a_plan_would_meet(agent):
    plan = "id: through\nsteps:\n  - skill: navigate\n    params: { to: workstation_B }\n"
    picture = await agent.call_tool("preview_scene", {"scene": "lab_blocked", "plan_yaml": plan})
    assert picture.content[0].type == "image"
    assert "1. step 0 (navigate): the leg from the start to workstation_B crosses obstacle 0" in picture.content[1].text
    assert (agent.root / "runs" / "maps" / "lab_blocked.png").is_file()


# --- plans -------------------------------------------------------------------------------------------------------
@with_agent
async def test_a_plan_is_checked_in_the_runs_own_words(agent):
    wrong = "id: lost\nsteps:\n  - skill: navigate\n    params: { to: pump_a, max_sped: 0.8 }\n"
    checked = await call(agent, "validate_plan", scene="yard_outdoor", plan_yaml=wrong, robot="go2")
    assert checked["errors"] == api.check_plan(api.parse_plan(wrong), "yard_outdoor", "go2").errors
    assert "did you mean 'pump_A'?" in " ".join(checked["errors"])
    no_arm = await call(agent, "validate_plan", scene="warehouse_small", plan="demo_material_runner", robot="go2")
    assert no_arm["errors"][0] == "step 3: skill 'pick' needs arm, which this robot does not have"
    through = "id: through\nsteps:\n  - skill: navigate\n    params: { to: workstation_B }\n"
    blocked = await call(agent, "validate_plan", scene="lab_blocked", plan_yaml=through)
    assert blocked["valid"] and [w["code"] for w in blocked["warnings"]] == ["leg_crosses_obstacle"]


@with_agent
async def test_a_plan_is_given_once_by_name_or_as_text(agent):
    both = {"scene": "lab_small", "plan": "demo_inspection_round", "plan_yaml": CHECK_THE_RACK}
    assert "one of the two" in await refusal(agent, "validate_plan", **both)
    assert "one of the two" in await refusal(agent, "run_plan", scene="lab_small")
    assert "not valid YAML" in await refusal(agent, "validate_plan", scene="lab_small", plan_yaml="id: [x\n")
    assert "one of the two" in await refusal(agent, "validate_scene")


# --- running -----------------------------------------------------------------------------------------------------
@with_agent
async def test_a_run_reports_its_steps_as_they_start(agent):
    seen = []

    async def progress(done: float, total: float | None, message: str | None) -> None:
        seen.append((done, total, message))

    result = await agent.call_tool(
        "run_plan", {"scene": "lab_small", "plan": "demo_inspection_round", "mode": "fake"}, progress_callback=progress
    )
    assert result.structured_content["status"] == "success"
    await asyncio.sleep(0.05)
    assert seen[0] == (0, 9, "step 1 of 9: say") and seen[-1][2] == "step 9 of 9: say"


@with_agent
async def test_a_failed_run_says_which_step_and_why_and_the_operator_is_a_fixed_answer(agent):
    """Perception that misses everything. Nobody is at the console: `on_failure` answers, and the request is
    counted. The model is never asked."""
    arguments = {"scene": "warehouse_small", "plan": "demo_material_runner", "mode": "fake", "noise": 1.0}
    report = await call(agent, "run_plan", **arguments)
    assert report["status"] == "aborted" and report["ok"] is False
    detect = next(s for s in report["summary"]["steps"] if s["skill"] == "detect")
    assert (detect["outcome"], detect["attempts"], detect["reason"]) == (
        "recoverable",
        3,
        "found 0 'red_box', expected 1",
    )
    assert report["summary"]["metrics"]["human_requests"] == 1
    skipped = await call(agent, "run_plan", **arguments, on_failure="skip")
    assert next(s for s in skipped["summary"]["steps"] if s["skill"] == "detect")["outcome"] == "skipped"


@with_agent
async def test_what_cannot_start_is_a_tool_error_in_spingis_words(agent):
    wrong = "id: t\nsteps:\n  - skill: say\n    params: {text: hi}\n    on_failre: {}\n"
    assert "on_failre: Extra inputs are not permitted" in await refusal(
        agent, "run_plan", scene="lab_small", plan_yaml=wrong
    )
    assert "videos are rendered by the simulator" in await refusal(
        agent, "run_plan", scene="lab_small", plan="demo_inspection_round", mode="fake", camera_video=True
    )
    assert (await agent.call_tool("run_plan", {"scene": "lab_small", "plan": "x", "mode": "hover"})).is_error
    assert not (agent.root / "runs").exists()


@with_agent
async def test_runs_are_done_one_at_a_time(agent):
    arguments = {"scene": "lab_small", "plan": "demo_inspection_round", "mode": "fake"}
    first, second = await asyncio.gather(call(agent, "run_plan", **arguments), call(agent, "run_plan", **arguments))
    assert first["status"] == second["status"] == "success" and first["run_id"] != second["run_id"]


@with_agent
async def test_bench_measures_a_plan_and_caps_the_runs(agent):
    report = await call(agent, "bench_plan", scene="lab_small", plan="demo_inspection_round", mode="fake", runs=3)
    assert (report["runs"], report["success_rate"], report["passed"]) == (3, 1.0, True)
    (written,) = (agent.root / "runs").glob("bench-*/report.json")
    assert json.loads(written.read_text())["plan_id"] == "demo_inspection_round"
    too_many = {"scene": "lab_small", "plan": "demo_inspection_round", "runs": 201}
    assert (await agent.call_tool("bench_plan", too_many)).is_error


# --- the working folder is the only folder -----------------------------------------------------------------------
@with_agent
async def test_files_are_read_inside_the_working_folder_and_among_the_samples(agent):
    outside = agent.root.parent / "outside.yaml"
    outside.write_text(ROOM)
    (agent.root / "link.yaml").symlink_to(outside)
    for ref in ("../outside.yaml", str(outside), "scenes/../../outside.yaml", "a\x00b"):
        for tool in ("read_scene", "describe_scene", "validate_scene"):
            assert "inside the working folder" in await refusal(agent, tool, scene=ref), (tool, ref)
        assert "inside the working folder" in await refusal(agent, "read_plan", plan=ref)
        assert "inside the working folder" in await refusal(agent, "run_plan", scene="lab_small", plan=ref)
    assert "is outside the working folder" in await refusal(agent, "read_scene", scene="link.yaml")

    (agent.root / "mine.yaml").write_text(ROOM)
    assert (await call(agent, "describe_scene", scene="mine.yaml"))["name"] == "mine"


@with_agent
async def test_scenes_are_written_only_under_scenes(agent):
    for name in ("../room", "a/b", "room.yaml", ""):
        assert "invalid scene name" in await refusal(agent, "save_scene", name=name, scene_yaml=ROOM)
    assert not (agent.root / "scenes").exists() and not (agent.root.parent / "room.yaml").exists()


@with_agent
async def test_an_episode_is_named_by_its_run_id_and_a_frame_stays_inside_it(agent):
    for run in ("../..", "/etc", "runs", "r-x/../../y"):
        assert "give a run id" in await refusal(agent, "get_episode", run=run)
        assert "give a run id" in await refusal(agent, "get_events", run=run)
    assert "no episode in" in await refusal(agent, "get_episode")  # nothing has run yet
    report = await call(agent, "run_plan", scene="lab_small", plan="demo_inspection_round", mode="fake")
    (agent.root / "secret.png").write_bytes(b"\x89PNG")
    for frame in ("../secret.png", "../../secret.png", "manifest.json", "frames/none.png"):
        assert "no frame" in await refusal(agent, "get_episode_frame", run=report["run_id"], frame=frame)


@with_agent
async def test_a_scene_or_a_plan_too_large_to_be_one_is_refused(agent):
    huge = "# " + "x" * MAX_YAML_BYTES + "\nlocations: {}\n"
    assert "larger than 256 kB" in await refusal(agent, "validate_scene", scene_yaml=huge)
    assert "larger than 256 kB" in await refusal(agent, "save_scene", name="big", scene_yaml=huge)
    assert "larger than 256 kB" in await refusal(agent, "validate_plan", scene="lab_small", plan_yaml=huge)


# --- for clients that offer resources and prompts ----------------------------------------------------------------
@with_agent
async def test_guides_and_the_scene_schema_are_resources_and_two_prompts_start_the_work(agent):
    guide = await agent.read_resource("spingi://guide/landmarks")
    assert guide.contents[0].text.startswith("# Landmarks")
    schema = json.loads((await agent.read_resource("spingi://schema/scene")).contents[0].text)
    assert schema["additionalProperties"] is False and "locations" in schema["properties"]
    assert {p.name for p in (await agent.list_prompts()).prompts} == {"create_scene", "plan_task"}
    prompt = await agent.get_prompt("create_scene", {"place": "a 6 by 4 m workshop"})
    assert "a 6 by 4 m workshop" in prompt.messages[0].content.text


# --- a cancelled call --------------------------------------------------------------------------------------------
async def test_a_cancelled_call_waits_for_the_run_to_stop_the_robot_and_frees_the_server():
    """The client gave up on the call. The run is cancelled in its own task and waited for, so the robot is
    stopped and the episode written before the next call can start."""
    busy, started, state = asyncio.Lock(), asyncio.Event(), {"robot_stopped": False}

    async def run():
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.02)  # stopping the robot takes a moment, and must not be cut short
            state["robot_stopped"] = True

    call_ = asyncio.create_task(_one_at_a_time(busy, run))
    await started.wait()
    call_.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call_
    assert state["robot_stopped"] and not busy.locked()


async def test_what_a_run_refuses_while_starting_is_a_tool_error():
    async def run():
        raise api.SceneError("invalid scene: obstacle: Extra inputs are not permitted")

    with pytest.raises(ToolError, match="obstacle: Extra inputs"):
        await _one_at_a_time(asyncio.Lock(), run)


# --- the real thing: a process, standard input and output --------------------------------------------------------
async def test_spingi_mcp_serves_over_stdio_and_keeps_its_output_for_the_protocol(tmp_path):
    """`spingi mcp` as a client launches it. A stray print on standard output would break the first message."""
    command = StdioServerParameters(
        command=sys.executable, args=["-m", "spingi.cli", "mcp", "--root", str(tmp_path)], cwd=str(Path.cwd())
    )
    async with Client(command) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS
        report = await call(client, "run_plan", scene="lab_small", plan="demo_inspection_round", mode="fake")
        assert report["status"] == "success" and Path(report["episode_dir"]).is_relative_to(tmp_path / "runs")
