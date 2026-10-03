import json
from pathlib import Path

from spingi.bench import bench, format_report
from spingi.cli import main
from spingi.core.human import ScriptedHuman
from spingi.session import SessionConfig, run_session

PLAN = Path("plans/demo_material_runner.yaml")
SCENE = Path("sim/scenes/warehouse_small.yaml")


async def test_session_on_fake_adapter_writes_an_episode(tmp_path):
    cfg = SessionConfig(plan=PLAN, scene=SCENE, runs_dir=tmp_path)
    result = await run_session(cfg, ScriptedHuman())
    assert result.ok and result.steps_completed == 8
    for name in ("manifest.json", "events.jsonl", "trajectory.jsonl", "plan.yaml", "scene.yaml"):
        assert (result.run_dir / name).exists()
    assert result.log.count("safety.armed") == 1


async def test_bench_without_noise_passes(tmp_path):
    report, runs = await bench(SessionConfig(plan=PLAN, scene=SCENE), 10, out_dir=tmp_path)
    assert report.passed and report.runs == 10 and len(runs) == 10
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["passed"] is True and (tmp_path / "runs.jsonl").read_text().count("\n") == 10
    assert "GATE PASSED" in format_report(report)


async def test_bench_with_heavy_noise_fails_and_counts_operator_requests(tmp_path):
    report, runs = await bench(
        SessionConfig(plan=PLAN, scene=SCENE, perception_noise=0.9), 10, out_dir=tmp_path, keep_failed_episodes=True
    )
    assert not report.passed and report.success_rate < 0.5
    assert report.needs_human_per_100 == 100 * sum(r.human_requests for r in runs) / 10
    assert any((tmp_path / "failed").iterdir())
    assert "GATE FAILED" in format_report(report)


async def test_bench_is_reproducible_with_the_same_seed():
    cfg = SessionConfig(plan=PLAN, scene=SCENE, perception_noise=0.5, seed=7)
    a, _ = await bench(cfg, 8)
    b, _ = await bench(cfg, 8)
    assert a.success_rate == b.success_rate and a.retries_per_run == b.retries_per_run


def test_cli_bench_gate_sets_the_exit_code(tmp_path, capsys):
    base = ["bench", str(PLAN), "--scene", str(SCENE), "--runs", "5", "--runs-dir", str(tmp_path), "--gate"]
    assert main(base) == 0
    assert main([*base, "--noise", "0.95"]) == 1
    assert "GATE FAILED" in capsys.readouterr().out
