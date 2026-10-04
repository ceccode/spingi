"""Every golden plan must actually run in simulation: the targets the LLM is measured against are feasible."""

from pathlib import Path

import pytest

from spingi.core.human import ScriptedHuman
from spingi.planner.evaluate import golden_plan, load_cases
from spingi.session import SessionConfig, run_session

CASES = load_cases("plans/golden/planner_cases.yaml")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_golden_plan_runs_in_sim(case, tmp_path):
    plan_file = tmp_path / f"{case.id}.yaml"
    import yaml

    plan_file.write_text(yaml.safe_dump(golden_plan(case).model_dump()))
    cfg = SessionConfig(plan=plan_file, scene=Path(case.scene), adapter="sim", runs_dir=tmp_path)
    result = await run_session(cfg, ScriptedHuman(default="continue"))
    assert result.ok, f"{case.id}: {result.status} after {result.steps_completed} steps"
