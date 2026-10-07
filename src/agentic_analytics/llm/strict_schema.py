"""Turning the engine's response schemas into OpenAI strict Structured Outputs.

The agents describe what they must return as Pydantic models, and those
models are the contract every provider answers: the scripted provider, a
local Ollama model, and the hosted one. That contract is not negotiable
here, because it is also what `model_validate` enforces after the answer
comes back.

OpenAI strict mode accepts a narrower JSON Schema than Pydantic emits, so
the two are reconciled in one deterministic pass at the provider boundary
rather than by rewriting eight models into a shape that only one provider
needs. The rules, from the documented requirements:

* every object sets ``additionalProperties: false``;
* every property of an object appears in ``required``;
* optionality is expressed as a nullable type, never by omission;
* composition keywords and ``default`` are not accepted.

  https://developers.openai.com/api/docs/guides/structured-outputs
  Reviewed 2026-09-27.

The one genuinely lossy rule is free-form objects. ``dict[str, Any]`` is an
object with arbitrary keys, and "arbitrary keys" is exactly what
``additionalProperties: false`` forbids -- under strict mode such a field
could only ever be ``{}``, which would silently break every tool call the
worker makes. Those fields are sent as strings holding JSON instead, and
the models that receive them accept either shape, so nothing about the
local providers changes. See `agents.schemas.JsonObject`.
"""

from __future__ import annotations

import json
from typing import Any

#: Keywords strict mode does not accept. Dropped rather than rejected: they
#: are descriptive, and Pydantic emits several of them routinely.
_DROPPED_KEYWORDS = frozenset(
    {
        "default",
        "title",
        "examples",
        "$comment",
        "deprecated",
        "readOnly",
        "writeOnly",
        "format",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "uniqueItems",
        "additionalItems",
        "unevaluatedProperties",
        "unevaluatedItems",
        "patternProperties",
        "propertyNames",
        "dependentSchemas",
        "dependentRequired",
        "if",
        "then",
        "else",
        "not",
        "allOf",
        "oneOf",
        "prefixItems",
        "contains",
        "minContains",
        "maxContains",
    }
)

#: Keywords carried through untouched when they appear beside a type.
_KEPT_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "const",
        "description",
        "minimum",
        "maximum",
        "pattern",
        "minItems",
        "maxItems",
    }
)

#: What a free-form object becomes. The description is part of the contract:
#: it is the only thing telling the model to emit a string rather than an
#: object, so it is not decoration.
_JSON_OBJECT_DESCRIPTION = (
    "A JSON object, encoded as a string. Emit valid JSON such as "
    '"{\\"key\\": \\"value\\"}", or "{}" when there is nothing to send.'
)


class StrictSchemaError(ValueError):
    """A schema that cannot be expressed in strict mode at all."""


def to_strict(schema: dict[str, Any], *, name: str = "response") -> dict[str, Any]:
    """Rewrite a Pydantic JSON Schema into an OpenAI strict schema.

    Deterministic and total: the same input always produces the same output,
    and anything it cannot express raises rather than being quietly dropped.
    `$defs` are preserved and `$ref` is left intact, since strict mode
    resolves references itself.
    """
    if not isinstance(schema, dict):
        raise StrictSchemaError(f"{name}: schema must be an object")
    converted = _convert(schema, path="$")
    if converted.get("type") != "object":
        # Strict mode requires an object at the root. Every response model
        # here is one, so this is a guard against a future model that is not
        # rather than a case needing a wrapper.
        raise StrictSchemaError(f"{name}: strict mode requires an object at the schema root")
    return converted


def _convert(node: dict[str, Any], *, path: str) -> dict[str, Any]:
    """One node, rewritten. Recurses into every subschema position."""
    nullable = False
    collapsed = _collapse_nullable_union(node, path=path)
    if collapsed is not None:
        node, nullable = collapsed

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROPPED_KEYWORDS:
            continue
        if key in ("properties", "$defs"):
            if not isinstance(value, dict):
                raise StrictSchemaError(f"{path}.{key}: expected an object")
            out[key] = {
                child: _convert(sub, path=f"{path}.{key}.{child}")
                for child, sub in value.items()
                if isinstance(sub, dict) or _raise_non_object(f"{path}.{key}.{child}")
            }
        elif key == "items":
            if not isinstance(value, dict):
                raise StrictSchemaError(f"{path}.items: array items must be a single schema")
            out[key] = _convert(value, path=f"{path}.items")
        elif key == "anyOf":
            out[key] = [
                _convert(sub, path=f"{path}.anyOf[{i}]")
                for i, sub in enumerate(value)
                if isinstance(sub, dict) or _raise_non_object(f"{path}.anyOf[{i}]")
            ]
        elif key in ("required", "additionalProperties"):
            # Both are decided below, from the converted properties, so a
            # value carried over from the source would be overwritten or --
            # worse, survive in a node that no longer matches it.
            continue
        elif key in _KEPT_KEYWORDS or key.startswith("$"):
            out[key] = value
        # Anything else is unrecognised. Dropping is the safe direction: an
        # unknown keyword is at most a lost constraint, while forwarding one
        # strict mode rejects fails the whole request.

    if _is_free_form_object(node):
        return _as_json_string(out, nullable=nullable, path=path)

    if out.get("type") == "object" or "properties" in out:
        properties = out.setdefault("properties", {})
        out["additionalProperties"] = False
        # Every property, in a stable order. Strict mode requires all of
        # them, and optionality has already become nullability above.
        out["required"] = sorted(properties)
        out.setdefault("type", "object")

    if nullable:
        out = _make_nullable(out, path=path)
    return out


def _raise_non_object(path: str) -> bool:
    raise StrictSchemaError(f"{path}: expected a schema object")


def _collapse_nullable_union(
    node: dict[str, Any], *, path: str
) -> tuple[dict[str, Any], bool] | None:
    """Turn ``anyOf: [X, {"type": "null"}]`` into X plus a nullable flag.

    This is the shape Pydantic emits for ``X | None``, and it is the shape
    strict mode does not accept. The documented replacement is a nullable
    type -- ``"type": ["string", "null"]`` -- so the union is collapsed back
    into its single real branch and the nullability applied afterwards.

    A union of two *real* branches is not collapsible and is refused, rather
    than being silently narrowed to one of them.
    """
    branches = node.get("anyOf")
    if not isinstance(branches, list):
        return None
    real = [b for b in branches if isinstance(b, dict) and b.get("type") != "null"]
    has_null = len(real) < len(branches)
    if not has_null:
        return None
    merged = {k: v for k, v in node.items() if k != "anyOf"}
    if len(real) == 1:
        merged.update(real[0])
        return merged, True
    # Several real branches. A union of bare types is still expressible --
    # `Scalar = str | int | float | bool | None` becomes one type array with
    # nothing lost. A branch carrying its own constraints is not: merging it
    # would drop the constraint, so that is refused rather than narrowed.
    plain = [b for b in real if set(b) == {"type"} and isinstance(b["type"], str)]
    if len(plain) != len(real):
        raise StrictSchemaError(
            f"{path}: strict mode cannot express a union of "
            f"{len(real)} constrained branches; model it as one type"
        )
    merged["type"] = list(dict.fromkeys(b["type"] for b in plain))
    return merged, True


def _is_free_form_object(node: dict[str, Any]) -> bool:
    """Whether this is ``dict[str, Any]`` rather than a described object.

    Pydantic emits an object with no `properties` and `additionalProperties`
    either true or absent. Strict mode cannot express it: forbidding
    additional properties on an object with no declared ones permits only
    the empty object.
    """
    if node.get("type") != "object":
        return False
    if node.get("properties"):
        return False
    return node.get("additionalProperties") is not False


def _as_json_string(out: dict[str, Any], *, nullable: bool, path: str) -> dict[str, Any]:
    """Represent a free-form object as a JSON-carrying string."""
    description = out.get("description")
    result: dict[str, Any] = {"type": "string", "description": _JSON_OBJECT_DESCRIPTION}
    if isinstance(description, str) and description:
        result["description"] = f"{description} {_JSON_OBJECT_DESCRIPTION}"
    return _make_nullable(result, path=path) if nullable else result


def _make_nullable(node: dict[str, Any], *, path: str) -> dict[str, Any]:
    """Add null to a node's type, the way strict mode expects.

    An enum has to gain `null` too, or the nullable type and the enumerated
    values contradict each other and nothing can satisfy both.
    """
    declared = node.get("type")
    if declared is None:
        # A bare `$ref` with no type beside it. Strict mode resolves the
        # reference itself and there is nowhere to attach null, so the
        # nullability cannot be expressed here.
        if "$ref" in node:
            raise StrictSchemaError(
                f"{path}: an optional $ref cannot be made nullable in strict mode; "
                "inline the referenced object or make the field required"
            )
        raise StrictSchemaError(f"{path}: cannot make a typeless schema nullable")
    types = declared if isinstance(declared, list) else [declared]
    if "null" not in types:
        types = [*types, "null"]
    node["type"] = types
    if isinstance(node.get("enum"), list) and None not in node["enum"]:
        node["enum"] = [*node["enum"], None]
    return node


def decode_json_object(value: Any) -> Any:
    """Accept either a JSON object or the string form of one.

    The mirror of `_as_json_string`, used by the response models so that a
    provider answering with a string and one answering with an object are
    both valid. Anything else is returned untouched for the model's own
    validation to reject, so this never turns a bad value into a plausible
    one.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except ValueError:
        return value
    return decoded
