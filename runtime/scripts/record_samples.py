"""Records the physics sample episodes the viewer offers (viewer/public/samples/), so that they can be remade after a
change in the runtime or the scenes.

Usage: uv run python scripts/record_samples.py

Each sample is a normal `spingi run --physics`; the head-camera frames are shrunk to 640x480 with a 256-colour palette
to keep the archives small (the viewer only shows them).
"""

import asyncio
import shutil
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from spingi.core.human import ScriptedHuman
from spingi.session import SessionConfig, run_session

OUT = Path(__file__).resolve().parents[2] / "viewer" / "public" / "samples"

SAMPLES = {
    "yard_patrol_go2_physics": SessionConfig(
        plan=Path("plans/yard_patrol.yaml"), scene=Path("sim/scenes/yard_outdoor.yaml"), adapter="sim", robot="go2"
    ),
    "test_track_g1_physics": SessionConfig(
        plan=Path("plans/test_track.yaml"), scene=Path("sim/scenes/test_track.yaml"), adapter="sim", robot="g1"
    ),
    "demo_inspection_round_g1_physics": SessionConfig(
        plan=Path("plans/demo_inspection_round.yaml"), scene=Path("sim/scenes/lab_small.yaml"), adapter="sim"
    ),
}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, cfg in SAMPLES.items():
        with tempfile.TemporaryDirectory() as tmp:
            cfg.runs_dir, cfg.locomotion = Path(tmp), "physics"
            result = await run_session(cfg, ScriptedHuman(default="abort"))
            for frame in (result.run_dir / "frames").glob("*.png"):
                img = Image.open(frame).convert("RGB").resize((640, 480), Image.LANCZOS)
                img.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(frame, optimize=True)
            target = OUT / f"{name}.zip"
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for p in sorted(result.run_dir.rglob("*")):
                    if p.is_file() and p.suffix != ".mp4":
                        zf.write(p, Path(name) / p.relative_to(result.run_dir))
            shutil.rmtree(result.run_dir, ignore_errors=True)
            size = target.stat().st_size / 1e6
            print(f"{name}: {result.status} {result.steps_completed}/{result.steps_total}, {size:.1f} MB")


if __name__ == "__main__":
    asyncio.run(main())
