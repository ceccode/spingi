# Architecture Decision Records

Every significant architectural decision is a file in this folder: `NNNN-title.md`. Fixed, short format: context, decision, consequences. A decision is changed by writing a new ADR that supersedes it, not by editing the old one.

| ADR | Title | Status | Milestone |
|-----|-------|--------|-----------|
| [0001](0001-no-ros2-in-core.md) | No ROS2 dependency in the core | Accepted | M0 |
| [0002](0002-python-pydantic-asyncio-stack.md) | Pure Python, pydantic, asyncio; MuJoCo for CI | Accepted | M0 |
| [0003](0003-llm-proposes-runtime-disposes.md) | The LLM proposes a declarative plan, the runtime executes it | Accepted | M0 |
| [0004](0004-plans-and-scenes-as-yaml.md) | Plans and scenes as YAML data, without control flow | Accepted | M0 |
| [0005](0005-jsonl-event-log-as-source-of-truth.md) | Append-only JSONL event log as the single source of truth | Accepted | M0 |
| [0006](0006-kinematic-sim-adapter-with-g1-model.md) | Kinematic SimAdapter in MuJoCo with the G1 model from mujoco_menagerie | Accepted | M1 |
| [0007](0007-predefined-kinematic-grasp.md) | Predefined grasp on standard containers, simulated kinematically | Accepted | M2 |

Still open (see the spec, section 13): vendor locomotion or pre-trained policy (M1, partially resolved by 0006), episode format vs LeRobot (M2), runtime on-board or on a laptop (M4), TUI or web console (M2).
