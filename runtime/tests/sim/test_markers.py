"""AprilTags rendered by MuJoCo and read back by MarkerPerceiver: the perception path a real camera will use,
exercised without a robot (M4.0). Rendering needs an OpenGL context; without one these tests skip."""

from pathlib import Path

import pytest

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import load_plan
from spingi.core.types import Pose2D
from spingi.perception.markers import MarkerPerceiver
from spingi.scenes import load_world
from spingi.session import SessionConfig, run_session
from spingi.skills import default_registry

cv2 = pytest.importorskip("cv2")

LAB = Path("sim/scenes/lab_small.yaml")
WAREHOUSE = Path("sim/scenes/warehouse_small.yaml")


async def look(adapter: SimAdapter, scene: Path, at: Pose2D):
    perceiver = MarkerPerceiver.for_adapter(adapter, scene)
    adapter.pose = at
    adapter._apply_pose()
    frame = await adapter.get_camera("head")
    if adapter.image(frame.id) is None:
        pytest.skip(f"no rendering: {adapter.render_error}")
    assert frame.camera_model is not None and frame.width == 1280
    return perceiver, await perceiver.detect(frame, "red_box")


async def test_the_g1_reads_the_tag_on_the_box_at_shelf_a():
    adapter = SimAdapter(LAB)
    perceiver, found = await look(adapter, LAB, Pose2D(x=0.0, y=2.0, yaw=1.57))
    adapter.close()
    assert [o.id for o in found] == ["red_box_01"] and found[0].marker_id == 7
    truth = adapter.object_positions()["red_box_01"]
    assert abs(found[0].pose.x - truth["x"]) < 0.01 and abs(found[0].pose.y - truth["y"]) < 0.01
    assert abs(found[0].pose.z - truth["z"]) < 0.01
    assert perceiver.last_detections[0].pixels > 40


async def test_the_long_face_of_a_box_is_read_within_its_half_extent():
    adapter = SimAdapter(WAREHOUSE)
    _, found = await look(adapter, WAREHOUSE, Pose2D(x=4.0, y=3.0, yaw=0.0))
    adapter.close()
    truth = adapter.object_positions()["red_box_01"]
    assert len(found) == 1  # the x face: the inset is half the short extent, so the centre is off by 2.5 cm
    assert abs(found[0].pose.x - truth["x"]) < 0.03 and abs(found[0].pose.y - truth["y"]) < 0.01


async def test_the_go2_cannot_see_a_box_on_top_of_a_shelf():
    adapter = SimAdapter(LAB, robot="go2")
    perceiver, found = await look(adapter, LAB, Pose2D(x=0.0, y=2.0, yaw=1.57))
    adapter.close()
    assert found == [] and perceiver.last_error is None  # the shelf's front edge hides the tag from a low camera


async def test_inspection_round_with_markers_sees_the_box_only_where_it_is(tmp_path):
    adapter = SimAdapter(LAB, record_dir=tmp_path)
    probe = await adapter.get_camera("head")
    if adapter.image(probe.id) is None:
        adapter.close()
        pytest.skip(f"no rendering: {adapter.render_error}")
    log = EventLog(run_id="r-markers")
    executor = Executor(
        default_registry(), adapter, MarkerPerceiver.for_adapter(adapter, LAB), ScriptedHuman(default="abort"), log=log
    )
    result = await executor.run(load_plan("plans/demo_inspection_round.yaml"), load_world(LAB))
    adapter.close()
    assert result.ok and result.steps_completed == 9
    assert [r.data["anomalies"] for r in log.find("perception.result")] == [[], ["present:red_box"]]
    assert "red_box_01" in result.final_state.objects
    assert len(list((tmp_path / "frames").glob("*.png"))) == 3  # the probe and the two inspections


async def test_a_session_records_which_perception_it_used(tmp_path):
    cfg = SessionConfig(
        plan=Path("plans/demo_inspection_round.yaml"), scene=LAB, adapter="sim", perception="markers", runs_dir=tmp_path
    )
    result = await run_session(cfg, ScriptedHuman(default="abort"))
    assert result.ok
    assert '"perception": "markers"' in (result.run_dir / "manifest.json").read_text()
