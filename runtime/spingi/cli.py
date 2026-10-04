"""Command line.

  spingi run <plan.yaml> [--scene S] [--adapter fake|sim] [--operator auto|console] [--view] [--record] [--zip] ...
  spingi bench <plan.yaml> [--scene S] [--adapter fake|sim] [--runs N] [--noise P] [--sigma M] [--gate]
  spingi export lerobot <episode_dir>... --out <dataset_dir>
  spingi plan "<request>" [--scene S] [--out plan.yaml] [--run ...]      (needs the llm extra and an API key)
  spingi eval-planner [--cases plans/golden/planner_cases.yaml]        (needs the llm extra and an API key)
  spingi replay <episode_dir> [<episode_dir>...]
  spingi skills

The MuJoCo viewer on macOS requires `uv run mjpython -m spingi.cli run ... --view`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from spingi.core.human import ScriptedHuman
from spingi.dotenv import load_dotenv
from spingi.session import SessionConfig, run_session
from spingi.skills import default_registry

DEFAULT_SCENE = Path("sim/scenes/lab_small.yaml")


def main(argv: list[str] | None = None) -> int:
    _, ignored = load_dotenv()  # runtime/.env only, allow-listed keys only, never overriding exported variables
    if ignored:
        print(
            f"runtime/.env: ignored variables not allowed in this file: {', '.join(sorted(ignored))}", file=sys.stderr
        )
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

    plan = sub.add_parser("plan", help="turn a request in natural language into a validated plan (LLM)")
    plan.add_argument("request")
    plan.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    plan.add_argument("--out", type=Path, help="write the plan to this YAML file")
    plan.add_argument("--model", default=None, help="Claude model id (default: claude-opus-5-5)")
    plan.add_argument("--run", action="store_true", help="run the plan right away (with --adapter, --operator)")
    plan.add_argument("--adapter", choices=["fake", "sim"], default="fake")
    plan.add_argument("--operator", choices=["auto", "console"], default="console")
    plan.add_argument("--yes", action="store_true", help="with --run: do not ask for confirmation before running")

    evalp = sub.add_parser("eval-planner", help="run the LLM planner on the golden cases and report the pass rate")
    evalp.add_argument("--cases", type=Path, default=Path("plans/golden/planner_cases.yaml"))
    evalp.add_argument("--model", default=None)
    evalp.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    evalp.add_argument("--min-pass-rate", type=float, default=1.0, help="exit with status 1 below this rate")

    replay = sub.add_parser("replay", help="run recorded episodes again and compare their behaviour")
    replay.add_argument("episodes", type=Path, nargs="+")

    sub.add_parser("skills", help="list the whitelisted skills and their parameters")
    args = parser.parse_args(argv)

    if args.cmd == "skills":
        registry = default_registry()
        for name in registry.names():
            print(name, registry.get(name).Params.model_json_schema().get("properties", {}))
        return 0
    if args.cmd == "export":
        return _export(args)
    if args.cmd == "plan":
        return _plan(args)
    if args.cmd == "eval-planner":
        return _eval_planner(args)
    if args.cmd == "replay":
        return asyncio.run(_replay(args))
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
    try:
        info = export_lerobot(args.episodes, args.out, fps=args.fps)
    except ValueError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"LeRobot dataset {info['codebase_version']}: {info['total_episodes']} episodes, "
        f"{info['total_frames']} frames at {info['fps']} Hz → {args.out}"
    )
    return 0


def _llm_planner(model: str | None):
    try:
        from spingi.planner.llm import DEFAULT_MODEL, LLMPlanner
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(f"missing dependency ({exc}); install the llm extra: uv sync --extra llm") from exc
    return LLMPlanner(default_registry(), model=model or DEFAULT_MODEL)


NO_CREDENTIALS = "no Anthropic credentials: put ANTHROPIC_API_KEY in runtime/.env (see .env.example) or export it"


def _api_call(fn):
    """Runs an LLM call and turns credential and API failures into one readable line."""
    import os

    import anthropic

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None, NO_CREDENTIALS  # checked up front, not inferred from an exception message
    try:
        return fn(), None
    except anthropic.AuthenticationError:
        return None, "the Anthropic API rejected the credentials (401)"
    except anthropic.RateLimitError as exc:
        return None, f"rate limited by the Anthropic API, retry after {exc.response.headers.get('retry-after', '?')} s"
    except anthropic.APIStatusError as exc:
        return None, f"Anthropic API error {exc.status_code}: {exc.message}"
    except anthropic.APIConnectionError:
        return None, "cannot reach the Anthropic API (network)"


def _plan(args) -> int:
    import yaml

    from spingi.planner.llm import PlanningError
    from spingi.scenes import load_routes, load_world

    planner = _llm_planner(args.model)
    try:
        result, error = _api_call(lambda: planner.plan(args.request, load_world(args.scene), load_routes(args.scene)))
    except PlanningError as exc:
        result, error = None, str(exc)
    if result is None:
        print(f"no plan: {error}", file=sys.stderr)
        return 1
    text = yaml.safe_dump(result.plan.model_dump(exclude_defaults=True), sort_keys=False, allow_unicode=True)
    print(f"# planned by {result.model} in {result.attempts} attempt(s)\n{text}")
    out = args.out
    if args.run and out is None:
        out = Path("runs") / f"plan-{result.plan.id}.yaml"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"plan written to {out}")
    if args.run:
        if not args.yes and not _confirm(f"Run this plan on the {args.adapter} adapter? [y/N] "):
            print("not run")
            return 0
        run_args = ["run", str(out), "--scene", str(args.scene), "--adapter", args.adapter, "--operator", args.operator]
        return main(run_args)
    return 0


def _confirm(question: str) -> bool:
    """A plan written by a model is shown and confirmed by a person before it moves anything."""
    if not sys.stdin.isatty():
        print("refusing to run a generated plan without confirmation: pass --yes", file=sys.stderr)
        return False
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _eval_planner(args) -> int:
    from spingi.planner.evaluate import evaluate, load_cases
    from spingi.scenes import load_routes, load_world

    planner = _llm_planner(args.model)
    cases = load_cases(args.cases)
    _, error = _api_call(lambda: planner.client.models.retrieve(planner.model))  # fail fast before the cases
    if error:
        print(error, file=sys.stderr)
        return 2

    def plan_fn(case):
        r = planner.plan(case.request, load_world(case.scene), load_routes(case.scene))
        return r.plan, r.attempts

    def progress(r) -> None:
        mark = "ok  " if r.passed else "FAIL"
        print(f"  {mark} {r.id:<22} {r.seconds:>5.1f}s  {r.error or '; '.join(r.differences)}", flush=True)

    print(f"planner {planner.model} on {len(cases)} cases from {args.cases}")
    report = evaluate(cases, plan_fn, default_registry(), planner.model, progress)
    print(f"\n{report.passed}/{report.cases} plans equivalent to the golden ones ({report.pass_rate:.0%})")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"report: {args.out}")
    return 0 if report.pass_rate >= args.min_pass_rate else 1


async def _replay(args) -> int:
    from spingi.replay import replay

    all_same = True
    for episode in args.episodes:
        report = await replay(episode)
        all_same &= report.same
        mark = "same" if report.same else "DIFFERENT"
        print(f"{mark:<9} {episode}  (recorded {report.recorded_status}, replayed {report.replayed_status})")
        for d in report.differences:
            print(f"          {d}")
    return 0 if all_same else 1


if __name__ == "__main__":
    sys.exit(main())
