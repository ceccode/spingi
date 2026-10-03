"""Command line.

  spingi run <plan.yaml> [--scene S] [--adapter fake|sim] [--operator auto|console] [--view] [--record] [--zip] ...
  spingi bench <plan.yaml> [--scene S] [--adapter fake|sim] [--runs N] [--noise P] [--sigma M] [--gate]
  spingi export lerobot <episode_dir>... --out <dataset_dir>
  spingi skills

The MuJoCo viewer on macOS requires `uv run mjpython -m spingi.cli run ... --view`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from spingi.core.human import ScriptedHuman
from spingi.session import SessionConfig, run_session
from spingi.skills import default_registry

DEFAULT_SCENE = Path("sim/scenes/lab_small.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spingi", description="Physical Agent Runtime for humanoid robots")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a plan once and write an episode")
    _session_args(run)
    run.add_argument(
        "--operator",
        choices=["auto", "console"],
        default="auto",
        help="who answers when a step needs a human: a fixed policy (auto) or you, on the terminal",
    )
    run.add_argument(
        "--on-failure", choices=["abort", "skip", "retry"], default="abort", help="answer used by --operator auto"
    )
    run.add_argument("--view", action="store_true", help="open the MuJoCo viewer (only with --adapter sim)")
    run.add_argument("--record", action="store_true", help="save a video of the run to runs/<id>/run.mp4")
    run.add_argument("--realtime", action="store_true", help="simulate in real time instead of as fast as possible")
    run.add_argument("--zip", action="store_true", help="also create runs/<id>.zip with the episode")
    run.add_argument("--quiet", action="store_true", help="print only the final summary")

    bench = sub.add_parser("bench", help="run a plan many times with perception noise and check the sim-to-real gate")
    _session_args(bench)
    bench.add_argument("--runs", type=int, default=50)
    bench.add_argument(
        "--on-failure",
        choices=["abort", "skip", "retry"],
        default="abort",
        help="answer to operator requests during the benchmark (each request is counted)",
    )
    bench.add_argument("--gate", action="store_true", help="exit with status 1 if the gate is not passed")
    bench.add_argument("--keep-failed", action="store_true", help="save the event log of failed runs")

    export = sub.add_parser("export", help="export episodes to another format")
    export_sub = export.add_subparsers(dest="format", required=True)
    lerobot = export_sub.add_parser("lerobot", help="LeRobotDataset v3.0 (state and action, no images yet)")
    lerobot.add_argument("episodes", type=Path, nargs="+", help="episode folders (runs/<run_id>)")
    lerobot.add_argument("--out", type=Path, required=True)
    lerobot.add_argument("--fps", type=int, default=10)

    sub.add_parser("skills", help="list the whitelisted skills and their parameters")
    args = parser.parse_args(argv)

    if args.cmd == "skills":
        registry = default_registry()
        for name in registry.names():
            print(name, registry.get(name).Params.model_json_schema().get("properties", {}))
        return 0
    if args.cmd == "export":
        return _export(args)
    if args.cmd == "bench":
        return asyncio.run(_bench(args))
    return asyncio.run(_run(args))


def _session_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("plan", type=Path)
    p.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    p.add_argument("--adapter", choices=["fake", "sim"], default="fake")
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    p.add_argument("--noise", type=float, default=0.0, help="perception false-negative rate, 0..1")
    p.add_argument("--sigma", type=float, default=0.0, help="perception position noise, metres")
    p.add_argument("--seed", type=int, default=0)


def _config(args) -> SessionConfig:
    return SessionConfig(
        plan=args.plan,
        scene=args.scene,
        adapter=args.adapter,
        runs_dir=args.runs_dir,
        perception_noise=args.noise,
        position_sigma_m=args.sigma,
        seed=args.seed,
    )


async def _run(args) -> int:
    cfg = _config(args)
    cfg.realtime, cfg.view, cfg.record_video, cfg.zip_episode = args.realtime, args.view, args.record, args.zip

    from spingi.core.events import EventLog
    from spingi.session import new_run_id

    run_id = new_run_id()
    log = EventLog(run_id=run_id, path=cfg.runs_dir / run_id / "events.jsonl")
    if args.operator == "console":
        from spingi.console import ConsoleHuman, ConsoleReporter

        human = ConsoleHuman()
        if not args.quiet:
            log.subscribe(ConsoleReporter())
        print("Operator console: Ctrl+C stops the robot.", flush=True)
    else:
        human = ScriptedHuman(default=args.on_failure)

    result = await run_session(cfg, human, log=log)

    if args.operator == "auto" and not args.quiet:
        for event in result.log.events:
            print(event.to_jsonl())
    extra = f" · video: {result.video}" if result.video else ""
    extra += f" · zip: {result.archive}" if result.archive else ""
    sim_info = f" · {result.sim_time_s:.1f} s simulated" if result.sim_time_s is not None else ""
    summary = f"{result.status.upper()} · {result.steps_completed}/{result.steps_total} steps{sim_info}"
    print(f"\n{summary} · episode: {result.run_dir}{extra}")
    return 0 if result.ok else 1


async def _bench(args) -> int:
    from spingi.bench import bench, format_report
    from spingi.session import new_run_id

    out_dir = args.runs_dir / f"bench-{new_run_id()[2:]}"

    def progress(done: int, total: int, m) -> None:
        mark = "." if m.ok else "x"
        print(mark, end="\n" if done % 50 == 0 or done == total else "", flush=True)

    report, _ = await bench(
        _config(args),
        args.runs,
        on_failure=args.on_failure,
        out_dir=out_dir,
        keep_failed_episodes=args.keep_failed,
        progress=progress,
    )
    print()
    print(format_report(report))
    print(f"\nreport: {out_dir / 'report.json'}")
    return 1 if args.gate and not report.passed else 0


def _export(args) -> int:
    try:
        from spingi.export.lerobot import export_lerobot
    except ImportError as exc:  # pragma: no cover - depends on the environment
        print(f"missing dependency ({exc}); install the export extra: uv sync --extra export", file=sys.stderr)
        return 2
    info = export_lerobot(args.episodes, args.out, fps=args.fps)
    print(
        f"LeRobot dataset {info['codebase_version']}: {info['total_episodes']} episodes, "
        f"{info['total_frames']} frames at {info['fps']} Hz → {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
