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
    assert (RUNTIME / "sim" / "models" / "unitree_g1" / "LICENSE").read_text() in served
