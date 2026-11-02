"""Records the sample episodes with a walking robot that the viewer offers (viewer/public/samples/), so that they can be
remade after a change in the runtime or the scenes.

Usage: uv run python scripts/record_samples.py

Each sample is a normal `spingi run --physics --camera-video` (the yard patrol: `--realistic`). To keep the archives
small, since the viewer only shows them, the head-camera frames are shrunk to 640x480: PNGs to a 256-colour palette,
the noisy camera's JPEGs re-encoded at quality 75. Frames that no event references (the second and third frame of
each landmark look) are left out. The head-camera video stays as it is.
"""

import asyncio
import json
import shutil
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from PIL import Image

from spingi.core.human import ScriptedHuman
from spingi.session import SessionConfig, run_session

OUT = Path(__file__).resolve().parents[2] / "viewer" / "public" / "samples"
PHYSICS = {"adapter": "sim", "locomotion": "physics", "record_camera": True}
REALISTIC = {**PHYSICS, "odometry": "drift", "camera_noise": True, "perception": "markers"}

SAMPLES = {
    "yard_patrol_go2_realistic": SessionConfig(
        plan=Path("plans/yard_patrol.yaml"), scene=Path("sim/scenes/yard_outdoor.yaml"), robot="go2", **REALISTIC
    ),
    "test_track_g1_physics": SessionConfig(
        plan=Path("plans/test_track.yaml"), scene=Path("sim/scenes/test_track.yaml"), robot="g1", **PHYSICS
    ),
    "demo_inspection_round_g1_physics": SessionConfig(
        plan=Path("plans/demo_inspection_round.yaml"), scene=Path("sim/scenes/lab_small.yaml"), **PHYSICS
    ),
}


def shrink(run_dir: Path) -> None:
    referenced = set()
    for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        ref = json.loads(line).get("frame_ref")
        if ref:
            referenced.add(Path(ref).name)
    for frame in (run_dir / "frames").glob("*"):
        if frame.name not in referenced:
            frame.unlink()
            continue
        img = Image.open(frame).convert("RGB").resize((640, 480), Image.LANCZOS)
        if frame.suffix == ".jpg":
            img.save(frame, quality=75, optimize=True)
        else:
            img.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(frame, optimize=True)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, cfg in SAMPLES.items():
        with tempfile.TemporaryDirectory() as tmp:
            result = await run_session(replace(cfg, runs_dir=Path(tmp)), ScriptedHuman(default="abort"))
            if (result.run_dir / "frames").is_dir():
                shrink(result.run_dir)
            target = OUT / f"{name}.zip"
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for p in sorted(result.run_dir.rglob("*")):
                    if p.is_file():
                        zf.write(p, Path(name) / p.relative_to(result.run_dir))
            shutil.rmtree(result.run_dir, ignore_errors=True)
            size = target.stat().st_size / 1e6
            print(f"{name}: {result.status} {result.steps_completed}/{result.steps_total}, {size:.1f} MB")


if __name__ == "__main__":
    asyncio.run(main())
