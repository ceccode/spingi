"""Golden episodes: recorded runs replayed on every commit. A difference means behaviour changed.

If the change is intended, re-record with `uv run python scripts/record_golden.py` and review the diff.
"""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("mujoco")

from spingi.core.events import EventLog
from spingi.replay import replay, signature

EPISODES = Path(__file__).parent / "episodes"
NAMES = sorted(p.name for p in EPISODES.iterdir() if p.is_dir())


def test_the_golden_set_covers_retries_and_operator_answers():
    events = {n: EventLog.read(EPISODES / n / "events.jsonl") for n in NAMES}
    assert sum(1 for e in events["material_runner_noisy"] if e.kind == "step.retry") >= 1
    answers = [e.data["action"] for e in events["blocked_wall_operator"] if e.kind == "human.response"]
    assert answers == ["retry", "abort"]


@pytest.mark.parametrize("name", NAMES)
async def test_golden_episode_replays_identically(name):
    report = await replay(EPISODES / name)
    assert report.same, "\n".join(report.differences)
    assert report.replayed_status == report.recorded_status


async def test_a_behaviour_change_is_detected(tmp_path):
    episode = tmp_path / "changed"
    shutil.copytree(EPISODES / "inspection_round", episode)
    plan = (episode / "plan.yaml").read_text().replace('checks: ["present:red_box"]', 'checks: ["absent:red_box"]')
    (episode / "plan.yaml").write_text(plan.replace("    params: { to: panel_C }\n", "    params: { to: dock }\n"))
    report = await replay(episode)
    assert not report.same and report.differences


def test_signature_ignores_timing():
    log = EventLog(run_id="r", clock=lambda: 123.0)
    log.emit("step.start", index=0, skill="navigate", params={})
    log.emit("skill.end", skill="navigate", outcome="success", duration_s=9.9)
    assert signature(log.events) == [("step.start", 0, "navigate"), ("skill.end", "navigate", "success")]
