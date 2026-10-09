"""The package ships copies of the repository's LICENSE and NOTICE (packaging tools ignore paths outside the
project); they must stay identical to the originals."""

from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
ROOT = RUNTIME.parent


def test_license_and_notice_copies_match_the_originals():
    for name in ("LICENSE", "NOTICE"):
        assert (RUNTIME / name).read_text() == (ROOT / name).read_text(), f"runtime/{name} differs from {name}"


def test_viewer_serves_the_third_party_notice():
    served = (ROOT / "viewer" / "public" / "NOTICE.txt").read_text()
    assert (ROOT / "NOTICE").read_text() in served
    for model in ("unitree_g1", "unitree_go2"):  # every vendored model's license, verbatim
        assert (RUNTIME / "sim" / "models" / model / "LICENSE").read_text() in served, model


def test_every_vendored_model_is_listed_in_the_notice():
    notice = (ROOT / "NOTICE").read_text()
    for model_dir in (RUNTIME / "sim" / "models").iterdir():
        if model_dir.is_dir():
            assert f"runtime/sim/models/{model_dir.name}" in notice, f"{model_dir.name} missing from NOTICE"
