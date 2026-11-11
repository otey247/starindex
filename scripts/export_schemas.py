"""Write the API component schemas as an OpenAPI 3.1 document for TypeScript generation.

Usage: python scripts/export_schemas.py [output.json]
The frontend runs `openapi-typescript` on the result (see frontend/package.json `gen:api`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from starindex import settings_store
from starindex.api import schemas
from starindex.providers import base as provider_base


def collect() -> list[type[BaseModel]]:
    seen: dict[str, type[BaseModel]] = {}
    for module in (schemas, provider_base, settings_store):
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                seen.setdefault(obj.__name__, obj)
    return list(seen.values())


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frontend/src/api/openapi-schemas.json")
    _, defs = models_json_schema(
        [(m, "serialization") for m in collect()], ref_template="#/components/schemas/{model}"
    )
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "StarIndex API schemas", "version": "1"},
        "paths": {},
        "components": {"schemas": defs.get("$defs", {})},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['components']['schemas'])} schemas to {out}")


if __name__ == "__main__":
    main()
