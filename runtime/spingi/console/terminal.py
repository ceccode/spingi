"""Terminal operator console: a reporter that prints the run as it happens and a HumanGateway that asks."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
import threading
from collections.abc import Callable
from typing import TextIO

from spingi.core.events import Event
from spingi.core.ports import HumanRequest, HumanResponse

_KEYS = {"retry": "r", "skip": "s", "abort": "a", "continue": "c"}
# C0/C1 control characters except tab: ANSI escapes in a plan, a reason or an LLM text could clear the screen or forge
# lines on the console that supervises the robot.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe(text: object) -> str:
    """Text from plans, events or a model, made inert for the terminal (newlines become spaces)."""
    return _CONTROL.sub("?", str(text).replace("\n", " ").replace("\r", " "))


async def _read_line(input_fn: Callable[[str], str], prompt: str) -> str:
    """input() in a daemon thread: an unanswered prompt never keeps the process alive at exit."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    def deliver(result: str | None, error: BaseException | None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result or "")

    def post(result: str | None, error: BaseException | None) -> None:
        # An answer typed after the run ended (timeout, Ctrl+C) has nobody to go to: drop it.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, result, error)

    def worker() -> None:
        try:
            line = input_fn(prompt)
        except BaseException as exc:  # noqa: BLE001 - EOFError, KeyboardInterrupt: handed to the waiting coroutine
            post(None, exc)  # bound now: `exc` is deleted when the block ends
        else:
            post(line, None)

    threading.Thread(target=worker, name="spingi-console-input", daemon=True).start()
    return await future


class ConsoleHuman:
    """Asks the operator on the terminal. Only the options the request offers are accepted (runtime-spec, section 4)."""

    def __init__(self, input_fn: Callable[[str], str] = input, out: TextIO | None = None) -> None:
        self._input = input_fn
        self._out = out or sys.stdout
        self.requests: list[HumanRequest] = []

    async def ask(self, request: HumanRequest, timeout_s: float) -> HumanResponse:
        self.requests.append(request)
        print(
            f"\n  OPERATOR NEEDED · step {request.step_index} ({safe(request.skill)})\n  {safe(request.reason)}",
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
                try:
                    raw = await _read_line(self._input, f"  {label}")
                except EOFError:  # stdin closed (piped input ended): the only safe answer is to stop
                    print("  no operator input available: aborting", file=self._out, flush=True)
                    return HumanResponse(action="abort", note="console: end of input")
                action = choices.get(raw.strip().lower())
                if action is not None:
                    return HumanResponse(action=action, note="console")
                print(f"  please answer one of: {keys}", file=self._out, flush=True)

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
            print(f"[{stamp}] {safe(line)}", file=self._out, flush=True)


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
