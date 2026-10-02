"""Regenerates docs/schemas/ from the episode's pydantic models."""

import json
from pathlib import Path

from spingi.episode import json_schemas

out = Path(__file__).resolve().parents[2] / "docs" / "schemas"
out.mkdir(parents=True, exist_ok=True)
for name, schema in json_schemas().items():
    (out / name).write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("written", out / name)
