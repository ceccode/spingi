"""Metrics computed from the event log only (ADR-0005), and the sim-to-real gate (runtime-spec, section 8.4)."""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from pydantic import BaseModel, Field

from spingi.core.events import Event

SAFETY_VIOLATIONS = ("safety.geofence", "safety.battery_low")


class RunMetrics(BaseModel):
    run_id: str
    status: str
    steps_completed: int
    steps_total: int
    sim_time_s: float | None = None
    wall_time_s: float
    human_requests: int
    retries: int
    safety_violations: int
    fatal: bool
    operator_stop: bool

    @property
    def ok(self) -> bool:
        return self.status == "success"


def run_metrics(events: list[Event], steps_total: int) -> RunMetrics:
    if not events:
        raise ValueError("no events")
    end = next((e for e in reversed(events) if e.kind == "run.end"), None)
    sim_times = [e.data["sim_t"] for e in events if "sim_t" in e.data]
    return RunMetrics(
        run_id=events[0].run_id,
        status=end.data.get("status", "unknown") if end else "unknown",
        steps_completed=end.data.get("steps_completed", 0) if end else 0,
        steps_total=steps_total,
        sim_time_s=max(sim_times) if sim_times else None,
        wall_time_s=round(events[-1].ts - events[0].ts, 3),
        human_requests=sum(1 for e in events if e.kind == "human.request"),
        retries=sum(1 for e in events if e.kind == "step.retry"),
        safety_violations=sum(1 for e in events if e.kind in SAFETY_VIOLATIONS),
        fatal=any(e.kind == "safety.estop" for e in events),
        operator_stop=any(e.kind == "operator.stop" for e in events),
    )


class Gate(BaseModel):
    """Thresholds a skill or plan must meet in simulation before it runs on the robot (runtime-spec 8.4)."""

    min_success_rate: float = 0.95
    max_needs_human_per_100: float = 5.0
    max_fatal_runs: int = 0
    max_safety_violations: int = 0


class BenchReport(BaseModel):
    plan_id: str
    scene: str
    adapter: str
    runs: int
    perception_noise: float
    position_sigma_m: float
    success_rate: float
    needs_human_per_100: float
    retries_per_run: float
    fatal_runs: int
    safety_violations: int
    sim_time_p50_s: float | None
    sim_time_p95_s: float | None
    failures: dict[str, int] = Field(default_factory=dict, description="status -> count, for runs that did not succeed")
    gate: Gate
    checks: dict[str, bool]
    passed: bool


def summarize(
    metrics: Iterable[RunMetrics],
    *,
    plan_id: str,
    scene: str,
    adapter: str,
    perception_noise: float,
    position_sigma_m: float,
    gate: Gate | None = None,
) -> BenchReport:
    runs = list(metrics)
    if not runs:
        raise ValueError("no runs to summarize")
    gate = gate or Gate()
    n = len(runs)
    successes = sum(r.ok for r in runs)
    times = sorted(r.sim_time_s for r in runs if r.ok and r.sim_time_s is not None)
    failures: dict[str, int] = {}
    for r in runs:
        if not r.ok:
            failures[r.status] = failures.get(r.status, 0) + 1
    report = dict(
        plan_id=plan_id,
        scene=scene,
        adapter=adapter,
        runs=n,
        perception_noise=perception_noise,
        position_sigma_m=position_sigma_m,
        success_rate=round(successes / n, 4),
        needs_human_per_100=round(100 * sum(r.human_requests for r in runs) / n, 2),
        retries_per_run=round(sum(r.retries for r in runs) / n, 3),
        fatal_runs=sum(r.fatal for r in runs),
        safety_violations=sum(r.safety_violations for r in runs),
        sim_time_p50_s=round(statistics.median(times), 2) if times else None,
        sim_time_p95_s=round(_percentile(times, 0.95), 2) if times else None,
        failures=failures,
        gate=gate,
    )
    checks = {
        "success_rate": report["success_rate"] >= gate.min_success_rate,
        "needs_human_per_100": report["needs_human_per_100"] <= gate.max_needs_human_per_100,
        "fatal_runs": report["fatal_runs"] <= gate.max_fatal_runs,
        "safety_violations": report["safety_violations"] <= gate.max_safety_violations,
    }
    return BenchReport(**report, checks=checks, passed=all(checks.values()))


def _percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)
