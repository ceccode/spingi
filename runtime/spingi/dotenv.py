"""Minimal `.env` loader, no dependency.

Security rules:
- Only `runtime/.env` of a source checkout is read, never a `.env` in the current directory: running `spingi`
  inside a downloaded folder must not let that folder change where API calls go.
- Only allow-listed variables are set (the API key and the run's own settings); anything else in the file is
  ignored and reported, so a `.env` cannot set ANTHROPIC_BASE_URL, PYTHONPATH, proxies or similar.
- A variable already exported in the shell always wins over the file.

Format: KEY=VALUE lines, optional `export ` prefix, `#` comments, single or double quotes.
"""

from __future__ import annotations

import os
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[1]
ALLOWED = {"ANTHROPIC_API_KEY"}
ALLOWED_PREFIX = "SPINGI_"


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


def default_env_file() -> Path | None:
    """`runtime/.env`, only when running from a source checkout (an installed package has no such file)."""
    if (RUNTIME_DIR / "pyproject.toml").is_file():
        return RUNTIME_DIR / ".env"
    return None


def load_dotenv(path: Path | None = None) -> tuple[list[str], list[str]]:
    """Loads allow-listed variables from `path` (default: runtime/.env). Returns (keys set, keys ignored)."""
    path = path if path is not None else default_env_file()
    if path is None or not path.is_file():
        return [], []
    loaded, ignored = [], []
    for key, value in parse(path.read_text(encoding="utf-8")).items():
        if key not in ALLOWED and not key.startswith(ALLOWED_PREFIX):
            ignored.append(key)
        elif key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded, ignored
