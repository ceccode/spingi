"""Converts the published walking policies into the plain arrays the simulator loads (runtime/sim/policies/).

Run once when a policy is added or updated; the output is committed, so nobody else needs these sources:

    uv run --with onnx python scripts/import_policies.py \
        --g1 <unitree_rl_mjlab>/deploy/robots/g1/config/policy/velocity/v0/exported/policy.onnx \
        --rl-sar <rl_sar>

- G1: unitree_rl_mjlab (Apache-2.0), velocity policy v0, an MLP 98 -> 29 with its observation normaliser. The ONNX
  metadata carries the joint order, gains, default pose and action scale.
- Go2, from rl_sar (Apache-2.0), exported with TorchScript; the weights are read straight from the archive (raw
  float32 tensors, in the order its data.pkl lists them), without torch; the settings come from each config.yaml.
  - go2/himloco: HIMLoco, trained on rough terrain. An estimator (270 -> 19) reads six frames of observations and
    guesses the body velocity and a terrain latent; the actor (64 -> 12) walks. It walks well but barely turns on
    the spot.
  - go2/robot_lab: an MLP 45 -> 12 that turns on the spot well. The simulator uses it for turning only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "sim" / "policies"


def g1(onnx_path: Path) -> None:
    import onnx
    from onnx import numpy_helper

    model = onnx.load(str(onnx_path))
    w = {i.name: numpy_helper.to_array(i) for i in model.graph.initializer}
    meta = {p.key: p.value for p in model.metadata_props}
    arrays = {}
    for n, i in enumerate((0, 2, 4, 6)):
        arrays[f"w{n}"] = w[f"mlp.{i}.weight"].astype(np.float32)
        arrays[f"b{n}"] = w[f"mlp.{i}.bias"].astype(np.float32)
    arrays["obs_mean"] = w["obs_normalizer._mean"][0].astype(np.float32)
    divisor = next(v for k, v in w.items() if k.startswith("onnx::Div"))  # the normaliser's std, as exported
    arrays["obs_std"] = divisor[0].astype(np.float32)
    floats = lambda key: [float(v) for v in meta[key].split(",")]  # noqa: E731
    config = {
        "name": "g1_mjlab_velocity",
        "robot": "unitree_g1",
        "source": f"unitree_rl_mjlab deploy/robots/g1/config/policy/velocity/v0 (run {meta.get('run_path', '?')})",
        "joint_names": meta["joint_names"].split(","),
        "default_q": floats("default_joint_pos"),
        "action_scale": floats("action_scale"),
        "kp": floats("joint_stiffness"),
        "kd": floats("joint_damping"),
        "actuation": "position",
        "observation": ["ang_vel", "gravity", "command", "phase", "joint_pos", "joint_vel", "last_action"],
        "obs_scale": {},
        "command_scale": [1.0, 1.0, 1.0],
        "command_limits": {"vx": [-0.5, 1.0], "vy": [-0.5, 0.5], "wz": [-1.0, 1.0]},
        "phase_period_s": 0.6,
        "stand_height_m": 0.8,
        "fall_height_m": 0.45,
    }
    write("g1_mjlab_velocity", arrays, config, onnx_path)


def _pt_tensors(pt_path: Path, shapes: list[tuple[int, ...]]) -> list[np.ndarray]:
    with zipfile.ZipFile(pt_path) as z:
        return [np.frombuffer(z.read(f"policy/data/{i}"), dtype="<f4").reshape(s).copy() for i, s in enumerate(shapes)]


GO2_COMMON = {
    "robot": "unitree_go2",
    "action_scale": [0.125, 0.25, 0.25] * 4,
    "actuation": "torque",
    "obs_scale": {"ang_vel": 0.25, "joint_vel": 0.05},
    "joint_damping": 0.1,
    "stand_height_m": 0.33,
    "fall_height_m": 0.12,
}


def go2_robot_lab(rl_sar: Path) -> None:
    shapes = [(512, 45), (512,), (256, 512), (256,), (128, 256), (128,), (12, 128), (12,)]
    t = _pt_tensors(rl_sar / "policy/go2/robot_lab/policy.pt", shapes)
    arrays = {f"{k}{n}": t[2 * n + (k == "b")] for n in range(4) for k in ("w", "b")}
    legs = ["FR", "FL", "RR", "RL"]  # the policy's joint order (rl_sar's go2 order)
    config = {
        **GO2_COMMON,
        "name": "go2_robot_lab",
        "source": "rl_sar policy/go2/robot_lab/policy.pt",
        "joint_names": [f"{leg}_{part}_joint" for leg in legs for part in ("hip", "thigh", "calf")],
        "default_q": [0.0, 0.8, -1.5] * 4,
        "kp": [20.0] * 12,
        "kd": [0.5] * 12,
        "observation": ["ang_vel", "gravity", "command", "joint_pos", "joint_vel", "last_action"],
        "command_scale": [1.0, 1.0, 1.0],
        "command_limits": {"vx": [-0.6, 1.0], "vy": [-0.5, 0.5], "wz": [-1.0, 1.0]},
    }
    write("go2_robot_lab", arrays, config, rl_sar / "policy/go2/robot_lab/policy.pt")


def go2_himloco(rl_sar: Path) -> None:
    shapes = [(512, 64), (512,), (256, 512), (256,), (128, 256), (128,), (12, 128), (12,)]
    shapes += [(128, 270), (128,), (64, 128), (64,), (19, 64), (19,)]
    t = _pt_tensors(rl_sar / "policy/go2/himloco/himloco.pt", shapes)
    arrays = {f"{k}{n}": t[2 * n + (k == "b")] for n in range(4) for k in ("w", "b")}
    arrays.update({f"e_{k}{n}": t[8 + 2 * n + (k == "b")] for n in range(3) for k in ("w", "b")})
    legs = ["FL", "FR", "RL", "RR"]  # legged_gym's order, the same as the MuJoCo model's
    config = {
        **GO2_COMMON,
        "name": "go2_himloco",
        "source": "rl_sar policy/go2/himloco/himloco.pt (HIMLoco)",
        "joint_names": [f"{leg}_{part}_joint" for leg in legs for part in ("hip", "thigh", "calf")],
        "default_q": [0.1, 0.8, -1.5, -0.1, 0.8, -1.5, 0.1, 1.0, -1.5, -0.1, 1.0, -1.5],
        "kp": [40.0] * 12,
        "kd": [1.0] * 12,
        "observation": ["command", "ang_vel", "gravity", "joint_pos", "joint_vel", "last_action"],
        "command_scale": [2.0, 2.0, 0.25],
        "command_limits": {"vx": [-0.6, 1.0], "vy": [-0.5, 0.5], "wz": [-1.0, 1.0]},
        "history": 6,
        "obs_clip": 100.0,
    }
    write("go2_himloco", arrays, config, rl_sar / "policy/go2/himloco/himloco.pt")


def write(name: str, arrays: dict[str, np.ndarray], config: dict, source: Path) -> None:
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(folder / "policy.npz", **arrays)
    (folder / "policy.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    sizes = ", ".join(f"{k} {tuple(v.shape)}" for k, v in arrays.items() if "w" in k)
    print(f"{name}: {sizes}; source sha256 {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--g1", type=Path, help="unitree_rl_mjlab's G1 velocity policy.onnx")
    parser.add_argument("--rl-sar", type=Path, help="a checkout of rl_sar (its policy/go2 folder)")
    args = parser.parse_args()
    if args.g1:
        g1(args.g1)
    if args.rl_sar:
        go2_himloco(args.rl_sar)
        go2_robot_lab(args.rl_sar)


if __name__ == "__main__":
    main()
