"""What a user gets from the package alone (ADR-0027).

Builds the wheel into a fresh folder, checks its size, installs it with the `sim` extra in a clean environment and
runs a sample in MuJoCo from an empty folder: no checkout on the path, no file from the repository in reach. Exits
with the status of the first step that fails. Needs only `uv` and the standard library.

    python scripts/wheel_smoke.py [extra ...]      # extras to install with the wheel; default: sim
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
MAX_BYTES = 60_000_000  # PyPI accepts 100 MB per file: fail well before, so growth is noticed
SAMPLE = ["spingi", "run", "demo_inspection_round", "--adapter", "sim", "--quiet", "--zip"]


def main(extras: list[str]) -> int:
    with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as empty:
        subprocess.run(["uv", "build", "--out-dir", out], cwd=RUNTIME, check=True)  # noqa: S603, S607 - uv from PATH
        (wheel,) = Path(out).glob("*.whl")
        size = wheel.stat().st_size
        print(f"{wheel.name}: {size / 1e6:.1f} MB")
        if size > MAX_BYTES:
            print(f"the wheel is larger than {MAX_BYTES / 1e6:.0f} MB", file=sys.stderr)
            return 1
        requirement = f"spingi[{','.join(extras)}] @ {wheel.as_uri()}"
        run = ["uv", "run", "--isolated", "--no-project", "--with", requirement, *SAMPLE]
        return subprocess.run(run, cwd=empty, check=False).returncode  # noqa: S603 - our own arguments


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["sim"]))
