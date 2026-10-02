"""Minimal CLI.

  spingi run <plan.yaml> [--adapter fake|sim] [--scene scene.yaml] [--view] [--record] [--realtime] [--zip]
  spingi skills

The MuJoCo viewer on macOS requires `uv run mjpython -m spingi.cli run ... --view`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import load_plan
from spingi.episode import write_episode, zip_episode
from spingi.perception.fake import FakePerceiver
from spingi.safety import SafetyMonitor
from spingi.scenes import load_safety_limits, load_world
from spingi.skills import default_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spingi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a plan")
    run.add_argument("plan", type=Path)
    run.add_argument("--adapter", choices=["fake", "sim"], default="fake")
    run.add_argument("--scene", type=Path, default=Path("sim/scenes/lab_small.yaml"))
    run.add_argument("--runs-dir", type=Path, default=Path("runs"))
    run.add_argument("--view", action="store_true", help="open the MuJoCo viewer (only with --adapter sim)")
    run.add_argument("--record", action="store_true", help="save a video of the run to runs/<id>/run.mp4")
    run.add_argument("--realtime", action="store_true", help="simulate in real time instead of as fast as possible")
    run.add_argument("--quiet", action="store_true", help="do not print the events")
    run.add_argument("--zip", action="store_true", help="also create runs/<id>.zip with the episode")
    run.add_argument(
        "--on-failure",
        choices=["abort", "skip", "retry"],
        default="abort",
        help="automatic answer to human requests (unattended CLI)",
    )
    sub.add_parser("skills", help="list the whitelisted skills and their parameters")
    args = parser.parse_args(argv)

    if args.cmd == "skills":
        registry = default_registry()
        for name in registry.names():
            print(name, registry.get(name).Params.model_json_schema().get("properties", {}))
        return 0

    return asyncio.run(_run(args))


async def _run(args) -> int:
    run_id = f"r-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    run_dir = args.runs_dir / run_id
    log = EventLog(run_id=run_id, path=run_dir / "events.jsonl")
    plan = load_plan(args.plan)
    state = load_world(args.scene)

    if args.adapter == "sim":
        from spingi.adapters.sim_mujoco import SimAdapter
        from spingi.perception.sim import SimPerceiver

        realtime = args.realtime or args.view
        robot = SimAdapter(
            args.scene, realtime=realtime, viewer=args.view, record_dir=run_dir, record_video=args.record
        )
        perceiver = SimPerceiver(robot)
        monitor_period = 0.05 if realtime else 0.0
    else:
        robot = FakeAdapter(start=state.robot.pose)
        perceiver = FakePerceiver()
        monitor_period = 0.05

    executor = Executor(
        registry=default_registry(),
        robot=robot,
        perceiver=perceiver,
        human=ScriptedHuman(default=args.on_failure),
        log=log,
    )
    monitor = SafetyMonitor(robot, log, load_safety_limits(args.scene), period_s=monitor_period)
    await monitor.start()
    try:
        result = await executor.run(plan, state)
    finally:
        await monitor.stop()
        video = robot.close() if hasattr(robot, "close") else None

    write_episode(
        run_dir,
        log=log,
        plan_path=args.plan,
        scene_path=args.scene,
        plan_id=plan.id,
        steps_total=len(plan.steps),
        status=result.status,
        steps_completed=result.steps_completed,
        adapter=robot,
        adapter_name=args.adapter if args.adapter == "fake" else "sim_mujoco",
        robot_model="unitree_g1" if args.adapter == "sim" else "fake",
    )
    archive = zip_episode(run_dir) if args.zip else None

    if not args.quiet:
        for event in log.events:
            print(event.to_jsonl())
    extra = f" · video: {video}" if video else ""
    extra += f" · zip: {archive}" if archive else ""
    sim_info = f" · {robot.sim_time_s:.1f} s simulated" if hasattr(robot, "sim_time_s") else ""
    summary = f"{result.status.upper()} · {result.steps_completed}/{len(plan.steps)} steps{sim_info}"
    print(f"\n{summary} · episode: {run_dir}{extra}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
