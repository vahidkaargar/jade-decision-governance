"""Runtime validation against the JSON Schema contracts (ADR-11). Imported lazily: jsonschema is slow to load."""

from __future__ import annotations

import json
from functools import lru_cache

from . import paths


@lru_cache(maxsize=1)
def _registry():
    from referencing import Registry, Resource

    resources = []
    for p in paths.schema_dir().glob("*.schema.json"):
        doc = json.loads(p.read_text())
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=8)
def validator(name: str):
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads((paths.schema_dir() / f"{name}.schema.json").read_text()), registry=_registry())


def validate(name: str, doc: dict) -> dict:
    validator(name).validate(doc)
    return doc
