# 0002: Pure Python, pydantic, asyncio; MuJoCo for CI

Status: Accepted · Date: 2026-10-01 · Milestone: M0

## Context
Three things are needed: validated contracts between components (the plan comes from an LLM, parameters must be checked), lightweight concurrency (watchdog, telemetry and console alongside execution), and a simulator that runs in CI on every commit. The official Unitree SDK, MuJoCo and the whole ML ecosystem are in Python. Isaac Lab requires an RTX GPU with at least 16 GB of VRAM and cannot run in CI.

## Decision
Python 3.11+. Every contract is a `pydantic.BaseModel`. Concurrency is `asyncio`, no threads in the core. The reference simulator for tests and CI is MuJoCo; Isaac Lab remains optional and is used only to train policies outside the runtime. The core dependencies are `pydantic` and `pyyaml`; everything else lives in the adapters.

## Consequences
Validation and JSON serialization come for free on every type; the plan produced by the LLM is rejected before it starts if it does not match the schema. Async tests use `pytest-asyncio`. Anyone adding a dependency to the core must justify it in an ADR. Python performance is not a concern in v0: the runtime orchestrates skills at low frequency, and joint control stays in the vendor's controller or in the policy.
