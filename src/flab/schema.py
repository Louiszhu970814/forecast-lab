"""Minimal JSON Schema validator for the project's own schemas (standard library only).

Supported keywords: type (string or list), const, enum, properties, required,
additionalProperties (bool), items, minItems, maxItems, minimum, maximum, minLength,
maxLength. Any other keyword in a schema is an error, so a schema can never rely on a
check this validator silently skips.
"""

from __future__ import annotations

import math
from typing import Any

SUPPORTED_KEYWORDS = frozenset({
    "$schema", "$id", "title", "description", "type", "const", "enum", "properties", "required",
    "additionalProperties", "items", "minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength",
})

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    # JSON Schema counts 1.0 as an integer.
    "integer": lambda v: not isinstance(v, bool) and (isinstance(v, int) or isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
}


def _equal(a: Any, b: Any) -> bool:
    """JSON equality: true is not 1, and 1 equals 1.0 (Python's == says True == 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return a == b


class SchemaError(ValueError):
    """The schema itself uses something this validator does not support."""


def check_schema(schema: Any, path: str = "#") -> None:
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: schema must be an object")
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        raise SchemaError(f"{path}: unsupported keywords {sorted(unknown)}")
    types = schema.get("type")
    for t in types if isinstance(types, list) else [types] if types is not None else []:
        if t not in _TYPES:
            raise SchemaError(f"{path}: unknown type {t!r}")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SchemaError(f"{path}: additionalProperties must be a boolean")
    for name, sub in (schema.get("properties") or {}).items():
        check_schema(sub, f"{path}/properties/{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}/items")


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Errors as ``"<path>: <message>"``; an empty list means the value is valid."""
    errors: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "const" in schema and not _equal(value, schema["const"]):
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and not any(_equal(value, x) for x in schema["enum"]):
        errors.append(f"{path}: must be one of {schema['enum']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}: missing {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected {key!r}")
        for key, sub in props.items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
    return errors
