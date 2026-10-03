"""Terminal operator console: a reporter that prints the run as it happens and a HumanGateway that asks."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import TextIO

from spingi.core.events import Event
from spingi.core.ports import HumanRequest, HumanResponse

_KEYS = {"retry": "r", "skip": "s", "abort": "a", "continue": "c"}


class ConsoleHuman:
    """Asks the operator on the terminal. Only the options the request offers are accepted (runtime-spec, section 4)."""

    def __init__(self, input_fn: Callable[[str], str] = input, out: TextIO | None = None) -> None:
        self._input = input_fn
        self._out = out or sys.stdout
        self.requests: list[HumanRequest] = []

    async def ask(self, request: HumanRequest, timeout_s: float) -> HumanResponse:
        self.requests.append(request)
        print(
            f"\n  OPERATOR NEEDED · step {request.step_index} ({request.skill})\n  {request.reason}",
            file=self._out,
            flush=True,
        )

        choices = {}
        for option in request.options:
            choices[option] = option
            choices[_KEYS[option]] = option
        label = "  ".join(f"[{_KEYS[o]}]{o[1:]}" for o in request.options) + " > "
        keys = ", ".join(_KEYS[o] for o in request.options)

        async def prompt() -> HumanResponse:
            while True:
                raw = await asyncio.to_thread(self._input, f"  {label}")
                action = choices.get(raw.strip().lower())
                if action is not None:
                    return HumanResponse(action=action, note="console")
                print(f"  please answer one of: {keys}", file=self._out, flush=True)

        # On timeout the pending input() keeps its thread until Enter is pressed; the run has moved on by then.
        return await asyncio.wait_for(prompt(), timeout=timeout_s)


class ConsoleReporter:
    """Subscribed to the EventLog: prints one short line per meaningful event."""

    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out or sys.stdout

    def __call__(self, event: Event) -> None:
        line = describe(event)
        if line is not None:
            t = event.data.get("sim_t")
            stamp = f"{t:7.1f}s" if isinstance(t, (int, float)) else "        "
            print(f"[{stamp}] {line}", file=self._out, flush=True)


def describe(event: Event) -> str | None:
    d = event.data
    k = event.kind
    if k == "run.start":
        return f"run {event.run_id} · plan {d.get('plan_id')} · {d.get('steps')} steps"
    if k == "step.start":
        return f"step {d.get('index')}  {d.get('skill')} {json.dumps(d.get('params', {}))}"
    if k == "skill.end" and d.get("outcome") != "success":
        return f"   {d.get('skill')} -> {d.get('outcome')}: {d.get('reason')}"
    if k == "step.retry":
        return f"   retry {d.get('attempt')}"
    if k == "say":
        return f'   says "{d.get("text")}"'
    if k == "perception.result":
        if "cls" in d:
            found = d.get("found") or []
            seen = ", ".join(found) if found else "nothing"
            return f"   looking for {d.get('cls')}: {seen}"
        anomalies = d.get("anomalies") or []
        verdict = "anomalies " + ", ".join(anomalies) if anomalies else "all checks passed"
        return f"   inspection at {d.get('target')}: {verdict}"
    if k == "safety.speed_capped":
        return f"   speed cap {d.get('applied')} m/s"
    if k.startswith("safety.") and k not in ("safety.armed", "safety.disarmed"):
        rest = {key: v for key, v in d.items() if key != "sim_t"}
        return f"   SAFETY {k[7:]} {json.dumps(rest)}"
    if k == "human.response":
        return f"   operator: {d.get('action')}"
    if k == "operator.stop":
        return "   OPERATOR STOP: e-stop engaged"
    if k == "run.end":
        return f"run end: {d.get('status')} · {d.get('steps_completed')} steps completed"
    return None
