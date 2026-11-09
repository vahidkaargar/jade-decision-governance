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


class Unreadable(ValueError):
    """A stored document that cannot be trusted. The message names the kind of fault and never carries any of the document's content."""


def load(name: str, text, *, id_field: str | None = None, id_value: str | None = None, validate: bool = True) -> dict:
    """Parse a persisted JSON document and validate it against its schema; `id_field`/`id_value` also require the document to be the row it
    was read from; `validate=False` skips the schema (a caller that still has more specific refusals to give, and validates after). Anything wrong raises `Unreadable`, so a reader fails closed with one exception type and a message safe to print."""
    from jsonschema import ValidationError

    try:
        doc = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise Unreadable("unreadable JSON") from None
    if not isinstance(doc, dict):
        raise Unreadable("not a JSON object")
    if validate:
        try:
            validator(name).validate(doc)
        except (ValidationError, RecursionError):
            raise Unreadable(f"fails the {name} schema") from None
    if id_field is not None and doc.get(id_field) != id_value:
        raise Unreadable("names a different id than its row")
    return doc
