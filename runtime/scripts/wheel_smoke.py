"""What a user gets from the package alone (ADR-0027).

Builds the wheel into a fresh folder, checks its size, installs it with its extras in a clean environment and, from
an empty folder, runs a sample in MuJoCo and reads what only the package can supply: a guide, a scene's description
and, with the `mcp` extra, the MCP server. No checkout on the path, no file from the repository in reach. Exits with
the status of the first step that fails. Needs only `uv` and the standard library.

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
PACKAGED = (
    "from spingi import api, assets; "
    "assert assets.ASSETS_DIR.name == '_assets', assets.ASSETS_DIR; "
    "assert api.guide('workflow') and len(api.guides()) == 4; "
    "assert api.describe_scene('yard_outdoor').terrain; "
    "print('guides and samples: ok')"
)
SERVER = "import spingi.mcp_server as m; m.build_server('.'); print('mcp server: ok')"


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
        clean = ["uv", "run", "--isolated", "--no-project", "--with", requirement]
        checks = [SAMPLE, ["python", "-c", PACKAGED], *([["python", "-c", SERVER]] if "mcp" in extras else [])]
        for check in checks:
            status = subprocess.run([*clean, *check], cwd=empty, check=False).returncode  # noqa: S603 - our own arguments
            if status != 0:
                return status
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["sim"]))
