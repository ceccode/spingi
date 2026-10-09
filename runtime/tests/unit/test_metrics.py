from spingi.core.events import EventLog
from spingi.metrics import Gate, run_metrics, summarize


def fake_run(run_id: str, status: str, human: int = 0, retries: int = 0, safety: int = 0, sim_t: float = 10.0):
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    log = EventLog(run_id=run_id, clock=clock)
    log.emit("run.start", plan_id="p", steps=3, sim_t=0.0)
    for _ in range(retries):
        log.emit("step.retry", index=0, skill="detect", attempt=1)
    for _ in range(human):
        log.emit("human.request", index=0, skill="detect", reason="x")
    for _ in range(safety):
        log.emit("safety.geofence", pose={"x": 9, "y": 0})
    log.emit("run.end", status=status, steps_completed=3 if status == "success" else 1, sim_t=sim_t)
    return log.events


def test_run_metrics_counts_from_the_event_log_only():
    m = run_metrics(fake_run("r1", "aborted", human=1, retries=2, safety=1, sim_t=12.5), steps_total=3)
    assert (m.status, m.steps_completed, m.human_requests, m.retries, m.safety_violations) == ("aborted", 1, 1, 2, 1)
    assert m.sim_time_s == 12.5 and not m.ok and not m.fatal


def test_summary_passes_the_gate_when_all_runs_succeed():
    runs = [run_metrics(fake_run(f"r{i}", "success", sim_t=10 + i), 3) for i in range(20)]
    r = summarize(runs, plan_id="p", scene="s", adapter="fake", perception_noise=0.0, position_sigma_m=0.0)
    assert r.passed and r.success_rate == 1.0 and r.needs_human_per_100 == 0
    assert r.sim_time_p50_s == 19.5 and r.sim_time_p95_s is not None and r.sim_time_p95_s > 27


def test_summary_fails_the_gate_on_each_threshold():
    ok = [run_metrics(fake_run(f"r{i}", "success"), 3) for i in range(18)]
    bad = [
        run_metrics(fake_run("x1", "aborted", human=1), 3),
        run_metrics(fake_run("x2", "aborted", human=1, safety=1), 3),
    ]
    r = summarize(ok + bad, plan_id="p", scene="s", adapter="fake", perception_noise=0.3, position_sigma_m=0.0)
    assert r.success_rate == 0.9 and r.needs_human_per_100 == 10.0 and r.safety_violations == 1
    assert r.checks == {
        "success_rate": False,
        "needs_human_per_100": False,
        "fatal_runs": True,
        "safety_violations": False,
    }
    assert not r.passed and r.failures == {"aborted": 2}
    lenient = Gate(min_success_rate=0.85, max_needs_human_per_100=10, max_safety_violations=1)
    assert summarize(
        ok + bad, plan_id="p", scene="s", adapter="fake", perception_noise=0.3, position_sigma_m=0.0, gate=lenient
    ).passed


def test_p95_is_checked_against_the_plan_budget_when_one_is_declared():
    runs = [run_metrics(fake_run(f"r{i}", "success", sim_t=10 + i), 3) for i in range(20)]
    base = dict(plan_id="p", scene="s", adapter="fake", perception_noise=0.0, position_sigma_m=0.0)
    r = summarize(runs, **base)
    assert "p95_within_budget" not in r.checks and r.passed
    r = summarize(runs, gate=Gate(max_p95_s=60.0), **base)
    assert r.checks["p95_within_budget"] and r.passed
    r = summarize(runs, gate=Gate(max_p95_s=20.0), **base)
    assert not r.checks["p95_within_budget"] and not r.passed
    failed = [run_metrics(fake_run(f"x{i}", "aborted"), 3) for i in range(3)]
    r = summarize(failed, gate=Gate(max_p95_s=60.0), **base)
    assert not r.checks["p95_within_budget"]  # no successful run: the budget cannot be shown to hold
