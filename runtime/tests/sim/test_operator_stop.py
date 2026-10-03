"""Ctrl+C during a run is the operator's stop button: e-stop, run ended as aborted, episode still written."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from spingi.core.human import ScriptedHuman
from spingi.episode import read_manifest
from spingi.session import SessionConfig, run_session

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")


async def test_sigint_stops_the_robot_and_still_writes_the_episode(tmp_path):
    cfg = SessionConfig(
        plan=Path("plans/demo_inspection_round.yaml"),
        scene=Path("sim/scenes/lab_small.yaml"),
        adapter="sim",
        runs_dir=tmp_path,
        realtime=True,  # wall-clock speed, so the run is still going when the signal arrives
    )
    loop = asyncio.get_running_loop()
    loop.call_later(0.4, os.kill, os.getpid(), signal.SIGINT)
    result = await run_session(cfg, ScriptedHuman())
    assert result.status == "aborted" and result.steps_completed < result.steps_total
    assert result.log.count("operator.stop") == 1
    assert result.log.last.kind == "safety.disarmed" or result.log.count("run.end") == 1
    assert read_manifest(result.run_dir).status == "aborted"
