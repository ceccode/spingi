"""StaticPlanner: loads a TaskPlan from YAML. Deterministic, no inference cost."""

from __future__ import annotations

from pathlib import Path

from spingi.core.plan import TaskPlan, load_plan


class StaticPlanner:
    def __init__(self, plans_dir: Path | str = "plans") -> None:
        self.plans_dir = Path(plans_dir)

    def plan(self, plan_id: str) -> TaskPlan:
        path = self.plans_dir / f"{plan_id}.yaml"
        return load_plan(path)

    def available(self) -> list[str]:
        return sorted(p.stem for p in self.plans_dir.glob("*.yaml"))
