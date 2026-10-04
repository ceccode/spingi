"""Minimal `.env` loader, no dependency.

Reads KEY=VALUE lines (optional `export ` prefix, `#` comments, single or double quotes) and sets each variable
only if it is not already in the environment: an exported variable always wins over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[1]


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_dotenv(*paths: Path) -> list[str]:
    """Loads the first existing file among `paths` (default: ./.env, then runtime/.env). Returns the keys set."""
    candidates = paths or (Path.cwd() / ".env", RUNTIME_DIR / ".env")
    for path in candidates:
        if path.is_file():
            loaded = []
            for key, value in parse(path.read_text(encoding="utf-8")).items():
                if key not in os.environ:
                    os.environ[key] = value
                    loaded.append(key)
            return loaded
    return []
