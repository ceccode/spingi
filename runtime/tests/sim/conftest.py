import pytest

pytest.importorskip("mujoco")

from pathlib import Path  # noqa: E402

MODEL = Path("sim/models/unitree_g1/g1.xml")
if not MODEL.exists():
    pytest.skip("G1 model missing", allow_module_level=True)
