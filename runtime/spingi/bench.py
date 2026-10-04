"""`spingi bench`: run a plan many times with perception noise and check the sim-to-real gate.

A benchmark run is a normal session (spingi.session) without an operator: every request for a human is
answered with a fixed policy, and counted. Episodes are not written unless asked, the event log is kept
in memory and turned into metrics.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from spingi.core.events import EventLog
from spingi.core.human import ScriptedHuman
from spingi.core.plan import load_plan
from spingi.metrics import BenchReport, Gate, RunMetrics, run_metrics, summarize
from spingi.session import SessionConfig, new_run_id, run_session


async def bench(
    base: SessionConfig,
    runs: int,
    *,
    on_failure: str = "abort",
    gate: Gate | None = None,
    out_dir: Path | None = None,
    keep_failed_episodes: bool = False,
    progress=None,
) -> tuple[BenchReport, list[RunMetrics]]:
    plan = load_plan(base.plan)
    metrics: list[RunMetrics] = []
    for i in range(runs):
        run_id = f"{new_run_id()}-{i:03d}"
        cfg = replace(base, seed=base.seed + i, write_episode=False, view=False, realtime=False, record_video=False)
        log = EventLog(run_id=run_id)
        result = await run_session(cfg, ScriptedHuman(default=on_failure), log=log)
        m = run_metrics(log.events, len(plan.steps))
        metrics.append(m)
        if keep_failed_episodes and not result.ok and out_dir is not None:
            failed = out_dir / "failed" / run_id
            failed.mkdir(parents=True, exist_ok=True)
            with (failed / "events.jsonl").open("w", encoding="utf-8") as fh:
                for e in log.events:
                    fh.write(e.to_jsonl() + "\n")
        if progress is not None:
            progress(i + 1, runs, m)

    report = summarize(
        metrics,
        plan_id=plan.id,
        scene=str(base.scene),
        adapter=base.adapter,
        perception_noise=base.perception_noise,
        position_sigma_m=base.position_sigma_m,
        gate=gate,
    )
    if out_dir is not None:  # a few KB written once at the end: blocking I/O is fine here
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (out_dir / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        with (out_dir / "runs.jsonl").open("w", encoding="utf-8") as fh:
            for m in metrics:
                fh.write(m.model_dump_json() + "\n")
    return report, metrics


def format_report(report: BenchReport) -> str:
    def mark(ok: bool) -> str:
        return "ok  " if ok else "FAIL"

    g = report.gate
    c = report.checks
    lines = [
        f"plan {report.plan_id} · scene {report.scene} · adapter {report.adapter} · {report.runs} runs",
        f"perception noise: false negatives {report.perception_noise:.0%}, position sigma {report.position_sigma_m} m",
        "",
        f"  {mark(c['success_rate'])}  success rate        {report.success_rate:.1%}"
        f"   (gate >= {g.min_success_rate:.0%})",
        f"  {mark(c['needs_human_per_100'])}  operator requests   {report.needs_human_per_100:.1f} per 100 runs"
        f"   (gate <= {g.max_needs_human_per_100:g})",
        f"  {mark(c['fatal_runs'])}  fatal runs          {report.fatal_runs}   (gate <= {g.max_fatal_runs})",
        f"  {mark(c['safety_violations'])}  safety violations   {report.safety_violations}"
        f"   (gate <= {g.max_safety_violations})",
        f"        retries per run     {report.retries_per_run:.2f}",
    ]
    if report.sim_time_p50_s is not None:
        lines.append(
            f"        simulated time       p50 {report.sim_time_p50_s:.1f} s · p95 {report.sim_time_p95_s:.1f} s"
        )
    if report.failures:
        lines.append(f"        failures             {json.dumps(report.failures)}")
    lines += ["", "GATE PASSED" if report.passed else "GATE FAILED"]
    return "\n".join(lines)
