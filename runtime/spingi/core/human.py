"""Scripted gateway to the operator, for tests and unattended runs. The interactive one is spingi.console."""

from __future__ import annotations

import asyncio
from collections import deque

from spingi.core.ports import HumanRequest, HumanResponse, OperatorAction

Action = OperatorAction


class ScriptedHuman:
    """Answers with a predefined sequence, then with `default`. For tests and the unattended CLI."""

    def __init__(
        self, responses: list[Action] | None = None, default: Action = "abort", never_answers: bool = False
    ) -> None:
        self._queue: deque[Action] = deque(responses or [])
        self.default = default
        self.never_answers = never_answers
        self.requests: list[HumanRequest] = []

    async def ask(self, request: HumanRequest, timeout_s: float) -> HumanResponse:
        self.requests.append(request)
        if self.never_answers:
            await asyncio.sleep(timeout_s)
            raise TimeoutError
        action = self._queue.popleft() if self._queue else self.default
        if action not in request.options:  # e.g. the default "abort" policy on a confirmation request is fine,
            action = "abort" if "abort" in request.options else request.options[0]  # "retry" on one is not
        return HumanResponse(action=action, note="scripted")
