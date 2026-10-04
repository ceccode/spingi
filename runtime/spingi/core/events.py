"""Structured, append-only event log (ADR-0005)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    ts: float
    run_id: str
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)

    def to_jsonl(self) -> str:
        flat = {"ts": self.ts, "run_id": self.run_id, "kind": self.kind, **self.data}
        return json.dumps(flat, ensure_ascii=False, default=str)

    @classmethod
    def from_jsonl(cls, line: str) -> Event:
        raw = json.loads(line)
        ts = raw.pop("ts")
        run_id = raw.pop("run_id")
        kind = raw.pop("kind")
        return cls(ts=ts, run_id=run_id, kind=kind, data=raw)


class EventLog:
    """In-memory list plus, when requested, a JSONL file opened in append mode."""

    def __init__(self, run_id: str, path: Path | None = None, clock=time.time, sim_clock=None) -> None:
        self.run_id = run_id
        self.events: list[Event] = []
        self._clock = clock
        self.sim_clock = sim_clock  # callable -> simulated seconds; adds `sim_t` to every event
        self._path = path
        self._subscribers: list = []
        self.subscriber_errors: list[str] = []  # observers removed after raising (e.g. a closed stdout pipe)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def subscribe(self, fn) -> None:
        """Calls `fn(event)` for every event emitted from now on (live console, metrics). Must not raise."""
        self._subscribers.append(fn)

    RESERVED = frozenset({"ts", "run_id", "kind"})

    def emit(self, kind: str, **data: Any) -> Event:
        clash = self.RESERVED.intersection(data)
        if clash:
            raise ValueError(f"event data cannot use reserved keys: {sorted(clash)}")
        if self.sim_clock is not None and "sim_t" not in data:
            data = {"sim_t": round(float(self.sim_clock()), 3), **data}
        event = Event(ts=self._clock(), run_id=self.run_id, kind=kind, data=data)
        self.events.append(event)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(event.to_jsonl() + "\n")
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001 - an observer (console, UI) must never break the run
                self._subscribers.remove(fn)
                self.subscriber_errors.append(f"{fn!r}: {exc!r}")
        return event

    def count(self, kind: str, **match: Any) -> int:
        return sum(1 for e in self.events if e.kind == kind and _matches(e, match))

    def find(self, kind: str, **match: Any) -> list[Event]:
        return [e for e in self.events if e.kind == kind and _matches(e, match)]

    @property
    def last(self) -> Event | None:
        return self.events[-1] if self.events else None

    @staticmethod
    def read(path: Path) -> list[Event]:
        with path.open(encoding="utf-8") as fh:
            return [Event.from_jsonl(line) for line in fh if line.strip()]


def _matches(event: Event, match: dict[str, Any]) -> bool:
    return all(event.data.get(k) == v for k, v in match.items())
