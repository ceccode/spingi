"""Prints the section of CHANGELOG.md for one version: the notes of its GitHub release.

    python scripts/release_notes.py 0.2.0

Exits with status 1 when the changelog has no section for that version: a release without notes is not released.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def section(version: str, text: str) -> str | None:
    """What is under `## <version>` or `## <version> (date)`, up to the next version's heading."""
    heading = re.compile(rf"^## {re.escape(version)}(?: \(.*\))?\s*$", re.MULTILINE)
    start = heading.search(text)
    if start is None:
        return None
    following = re.search(r"^## ", text[start.end() :], re.MULTILINE)
    end = start.end() + following.start() if following else len(text)
    return text[start.end() : end].strip()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    notes = section(argv[0], CHANGELOG.read_text(encoding="utf-8"))
    if not notes:
        print(f"CHANGELOG.md has no section for {argv[0]}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
