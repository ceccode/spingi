from spingi.core.types import ObjectRef, Pose2D, StateDelta, WorldState, apply_delta
from tests.conftest import red_box, world


def test_world_state_roundtrips_through_json():
    state = world(objects={"red_box_01": red_box()})
    again = WorldState.model_validate(state.model_dump())
    assert again == state


def test_apply_delta_is_pure_and_updates_fields():
    state = world()
    delta = StateDelta(robot_pose=Pose2D(x=1, y=1), battery_pct=80, objects_upsert={"b": ObjectRef(id="b", cls="box")})
    new = apply_delta(state, delta)
    assert state.robot.pose.x == 0 and state.robot.battery_pct == 100
    assert new.robot.pose.x == 1 and new.robot.battery_pct == 80
    assert "b" in new.objects and "b" not in state.objects


def test_apply_delta_clear_holding_wins_over_holding():
    state = world()
    state.robot.holding = red_box()
    new = apply_delta(state, StateDelta(clear_holding=True, holding=red_box()))
    assert new.robot.holding is None


def test_apply_none_delta_returns_same_state():
    state = world()
    assert apply_delta(state, None) is state


def test_nan_and_infinity_are_rejected_in_geometry():
    import math

    import pytest
    from pydantic import ValidationError

    from spingi.core.types import Pose2D, Pose3D
    from spingi.safety import Geofence

    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            Pose2D(x=bad, y=0)
        with pytest.raises(ValidationError):
            Pose3D(x=0, y=0, z=bad)
        with pytest.raises(ValidationError):
            Geofence(x_min=-1, x_max=bad, y_min=-1, y_max=1)


def test_names_use_a_safe_alphabet():
    import pytest
    from pydantic import ValidationError

    from spingi.core.types import Location, ObjectRef, Pose2D

    with pytest.raises(ValidationError):
        Location(name="dock<script>", pose=Pose2D(x=0, y=0))
    with pytest.raises(ValidationError):
        ObjectRef(id="box 1", cls="box")
