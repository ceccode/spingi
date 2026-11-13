# Architecture Decision Records

Every significant architectural decision is a file in this folder: `NNNN-title.md`. Fixed, short format: context, decision, consequences. A decision is changed by writing a new ADR that supersedes it, not by editing the old one.

| ADR | Title | Status | Milestone |
|-----|-------|--------|-----------|
| [0001](0001-no-ros2-in-core.md) | No ROS2 dependency in the core | Accepted | M0 |
| [0002](0002-python-pydantic-asyncio-stack.md) | Pure Python, pydantic, asyncio; MuJoCo for CI | Accepted | M0 |
| [0003](0003-llm-proposes-runtime-disposes.md) | The LLM proposes a declarative plan, the runtime executes it | Accepted | M0 |
| [0004](0004-plans-and-scenes-as-yaml.md) | Plans and scenes as YAML data, without control flow | Accepted | M0 |
| [0005](0005-jsonl-event-log-as-source-of-truth.md) | Append-only JSONL event log as the single source of truth | Accepted | M0 |
| [0006](0006-kinematic-sim-adapter-with-g1-model.md) | Kinematic SimAdapter in MuJoCo with the G1 model from mujoco_menagerie | Accepted, amended by 0015 | M1 |
| [0007](0007-predefined-kinematic-grasp.md) | Predefined grasp on standard containers, simulated kinematically | Accepted | M2 |
| [0008](0008-terminal-operator-console.md) | Operator console v0 in the terminal | Accepted | M2 |
| [0009](0009-lerobot-export-derived-format.md) | Episodes stay the source of truth; LeRobot v3.0 is an export | Accepted | M2 |
| [0010](0010-llm-planner-structured-output.md) | LLM planner with structured output, validated like any plan | Accepted | M3 |
| [0011](0011-robot-time-and-terminal-estop.md) | Robot time, terminal e-stop and latched safety stops | Accepted | M3+ |
| [0012](0012-robot-profiles-and-capabilities.md) | Robot profiles and capabilities; the Go2 quadruped as a second robot | Accepted | M3+ |
| [0013](0013-locomotion-vendor-controller.md) | Locomotion from the vendor's controller; the simulator stays kinematic | Proposed; simulator part superseded by 0015 | M4.0 |
| [0014](0014-runtime-on-a-laptop-then-on-board.md) | The runtime on a laptop in the lab, on-board for a pilot | Proposed | M4.0 |
| [0015](0015-physics-locomotion-and-outdoor-ground.md) | Physics locomotion and outdoor ground in the simulator | Accepted, amended by 0016 | M4.0 |
| [0016](0016-drifting-odometry-landmarks-and-camera-noise.md) | What the robot knows: drifting odometry, landmarks and a noisy camera | Accepted | M4.0 |
| [0017](0017-head-camera-video.md) | What the robot sees, continuously: the head camera as a video | Accepted | M4.0 |
| [0018](0018-viewer-checks-at-load-and-modules.md) | The viewer checks an episode once, when it loads, and is split by reason to change | Accepted | M4.0 |
| [0019](0019-biome-for-the-viewer.md) | Biome formats and lints the viewer | Accepted | M4.0 |
| [0020](0020-sim-adapter-delegates.md) | SimAdapter delegates contacts, objects, frames and videos | Accepted | M4.0 |
| [0021](0021-a-test-file-for-every-module.md) | Every module has its own tests | Accepted | M4.0 |
| [0022](0022-browser-tests-in-ci.md) | The viewer is tested in a browser, in CI | Accepted | M4.0 |

Proposed, to be accepted before the first robot (M4.1): 0013 (locomotion on the robot) and 0014 (where the runtime runs), written in M4.0, the prerequisites milestone that needs no robot. 0015 replaced the simulator half of 0013: the simulator can now walk the robots with learned policies on outdoor ground, while the robot itself will still walk with the vendor's controller. Q14 of the spec (perception on the real robot) is answered in simulation by 0016: `navigate` corrects its drifting pose with AprilTags at known places, read by `MarkerPerceiver`; the drift figures and the tag plan are to be checked with the first lab results. Multi-robot missions (a dog scouting for a humanoid) are out of scope for v0 and would need their own ADR after M4 (see 0012).

New ADRs start from [template.md](template.md).
