"""Regenerates docs/schemas/ from the pydantic models of the episode and of the scene file."""

import json
from pathlib import Path

from spingi.episode import json_schemas
from spingi.scenes import json_schema as scene_schema

out = Path(__file__).resolve().parents[2] / "docs" / "schemas"
out.mkdir(parents=True, exist_ok=True)
for name, schema in {**json_schemas(), "scene.schema.json": scene_schema()}.items():
    (out / name).write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("written", out / name)
