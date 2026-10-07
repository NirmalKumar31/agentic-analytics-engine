"""Every response schema, as a strict provider will see it.

OpenAI strict Structured Outputs accepts a narrower JSON Schema than
Pydantic emits, and the gap is not cosmetic: eighty-eight violations sat
across the eight response models before this existed. A schema the provider
rejects fails the first call of whichever agent owns it -- possibly minutes
into a paid run, and not necessarily the first agent to run, so the
invariants are asserted here rather than discovered there.

The rules, from the documented requirements (reviewed 2026-09-27):
https://developers.openai.com/api/docs/guides/structured-outputs
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentic_analytics.agents.base import active_response_schemas
from agentic_analytics.llm.strict_schema import (
    StrictSchemaError,
    decode_json_object,
    to_strict,
)

#: Keywords strict mode does not accept anywhere in a schema.
FORBIDDEN = (
    "allOf",
    "oneOf",
    "not",
    "anyOf",
    "default",
    "prefixItems",
    "unevaluatedProperties",
    "patternProperties",
    "propertyNames",
    "dependentSchemas",
    "if",
    "then",
    "else",
)


def _violations(node: Any, path: str = "$") -> list[str]:
    """Every strict-mode violation reachable from this node."""
    found: list[str] = []
    if not isinstance(node, dict):
        return found

    declared = node.get("type")
    types = declared if isinstance(declared, list) else [declared]
    if "object" in types:
        properties = set(node.get("properties", {}))
        if node.get("additionalProperties") is not False:
            found.append(f"{path}: additionalProperties is not false")
        missing = properties - set(node.get("required", []))
        if missing:
            found.append(f"{path}: not in required: {sorted(missing)}")

    for keyword in FORBIDDEN:
        if keyword in node:
            found.append(f"{path}: forbidden keyword {keyword!r}")

    for key, value in node.items():
        if key in ("properties", "$defs") and isinstance(value, dict):
            for child, sub in value.items():
                found += _violations(sub, f"{path}.{key}.{child}")
        elif key == "items":
            found += _violations(value, f"{path}.items")
    return found


SCHEMAS = active_response_schemas()


def test_the_registry_is_not_empty() -> None:
    """A registry that silently emptied would make every test below pass."""
    assert len(SCHEMAS) >= 7
    assert "ToolChoice" in SCHEMAS
    assert "AnalysisPlan" in SCHEMAS


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_active_schema_converts_to_strict(name: str) -> None:
    """The conversion itself must not raise for any live schema."""
    assert to_strict(SCHEMAS[name], name=name)["type"] == "object"


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_active_schema_satisfies_strict_invariants(name: str) -> None:
    found = _violations(to_strict(SCHEMAS[name], name=name))
    assert not found, f"{name}: {found}"


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_the_conversion_is_deterministic(name: str) -> None:
    """The same schema must not produce two different requests.

    A schema that varied between calls would make a token count taken over
    one of them meaningless for the other.
    """
    first = json.dumps(to_strict(SCHEMAS[name]), sort_keys=True)
    second = json.dumps(to_strict(SCHEMAS[name]), sort_keys=True)
    assert first == second


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_conversion_does_not_mutate_its_input(name: str) -> None:
    """The canonical schema is shared; rewriting it in place would leak."""
    before = json.dumps(SCHEMAS[name], sort_keys=True)
    to_strict(SCHEMAS[name])
    assert json.dumps(SCHEMAS[name], sort_keys=True) == before


# ------------------------------------------------------------ the rules
def test_optionality_becomes_nullability() -> None:
    """Strict mode requires every property, so `X | None` cannot be omitted.

    Pydantic emits `anyOf: [{"type": "X"}, {"type": "null"}]`; the documented
    strict form is a nullable type array.
    """
    schema = {
        "type": "object",
        "properties": {"note": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    }
    out = to_strict(schema)
    assert out["properties"]["note"]["type"] == ["string", "null"]
    assert out["required"] == ["note"]


def test_a_nullable_enum_gains_null_as_a_member() -> None:
    """Otherwise the type and the enumeration contradict each other."""
    schema = {
        "type": "object",
        "properties": {
            "grain": {"anyOf": [{"type": "string", "enum": ["day", "week"]}, {"type": "null"}]}
        },
    }
    out = to_strict(schema)["properties"]["grain"]
    assert out["type"] == ["string", "null"]
    assert None in out["enum"]


def test_a_union_of_bare_types_becomes_a_type_array() -> None:
    """`str | int | float | bool | None` is expressible and must not be lost.

    This is the evidence-cell value type, so narrowing it would silently
    stop a finding from citing a number.
    """
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                    {"type": "number"},
                    {"type": "boolean"},
                    {"type": "null"},
                ]
            }
        },
    }
    out = to_strict(schema)["properties"]["value"]
    assert out["type"] == ["string", "integer", "number", "boolean", "null"]


def test_a_union_of_constrained_branches_is_refused_not_narrowed() -> None:
    """Silently dropping one branch's constraints would be the worse bug."""
    schema = {
        "type": "object",
        "properties": {
            "x": {
                "anyOf": [
                    {"type": "string", "enum": ["a"]},
                    {"type": "integer", "minimum": 3},
                    {"type": "null"},
                ]
            }
        },
    }
    with pytest.raises(StrictSchemaError, match="constrained branches"):
        to_strict(schema)


def test_defaults_and_titles_are_dropped() -> None:
    schema = {
        "type": "object",
        "title": "Thing",
        "properties": {"n": {"type": "integer", "default": 3, "title": "N"}},
    }
    out = to_strict(schema)
    assert "title" not in out
    assert "default" not in out["properties"]["n"]


def test_nested_objects_are_converted_too() -> None:
    """A violation three levels down fails the request just as hard."""
    schema = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "inner": {"type": "object", "properties": {"x": {"type": "string"}}}
                },
            }
        },
    }
    out = to_strict(schema)
    inner = out["properties"]["outer"]["properties"]["inner"]
    assert inner["additionalProperties"] is False
    assert inner["required"] == ["x"]


def test_array_items_are_converted() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {"type": "object", "properties": {"a": {"type": "string"}}},
            }
        },
    }
    items = to_strict(schema)["properties"]["rows"]["items"]
    assert items["additionalProperties"] is False
    assert items["required"] == ["a"]


def test_defs_are_preserved_and_refs_left_alone() -> None:
    """Strict mode resolves `$ref` itself; rewriting one would break it."""
    schema = {
        "type": "object",
        "properties": {"cell": {"$ref": "#/$defs/Cell"}},
        "$defs": {"Cell": {"type": "object", "properties": {"v": {"type": "string"}}}},
    }
    out = to_strict(schema)
    assert out["properties"]["cell"]["$ref"] == "#/$defs/Cell"
    assert out["$defs"]["Cell"]["additionalProperties"] is False


def test_a_non_object_root_is_refused() -> None:
    with pytest.raises(StrictSchemaError, match="object at the schema root"):
        to_strict({"type": "array", "items": {"type": "string"}})


# --------------------------------------------------- free-form objects
def test_a_free_form_object_becomes_a_json_string() -> None:
    """`dict[str, Any]` cannot exist under strict mode.

    `additionalProperties: false` is mandatory, and on an object with no
    declared properties it permits only `{}`, so a tool-call arguments
    field would be silently emptied rather than rejected.
    """
    schema = {"type": "object", "properties": {"arguments": {"type": "object"}}}
    out = to_strict(schema)["properties"]["arguments"]
    assert out["type"] == "string"
    assert "JSON object" in out["description"]


def test_a_nullable_free_form_object_is_a_nullable_string() -> None:
    schema = {
        "type": "object",
        "properties": {
            "change": {
                "anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]
            }
        },
    }
    out = to_strict(schema)["properties"]["change"]
    assert out["type"] == ["string", "null"]


def test_a_described_object_is_not_treated_as_free_form() -> None:
    """Only objects with no declared properties are converted."""
    schema = {
        "type": "object",
        "properties": {"named": {"type": "object", "properties": {"a": {"type": "string"}}}},
    }
    assert to_strict(schema)["properties"]["named"]["type"] == "object"


def test_the_tool_arguments_field_survives_the_round_trip() -> None:
    """The contract that matters: what the worker sends a tool.

    The schema asks for a string, the model answers with one, and the model
    object must come back out as the dict the MCP client will pass along.
    """
    from agentic_analytics.agents.worker import ToolChoice

    strict = to_strict(SCHEMAS["ToolChoice"])
    assert strict["properties"]["arguments"]["type"] == "string"

    choice = ToolChoice.model_validate(
        {"done": False, "tool": "profile_table", "arguments": '{"table": "orders", "limit": 5}'}
    )
    assert choice.arguments == {"table": "orders", "limit": 5}


def test_an_object_is_still_accepted_for_a_json_field() -> None:
    """Ollama and the scripted provider answer with an object, not a string.

    Both shapes have to validate, or making the schema strict for one
    provider would break the other two.
    """
    from agentic_analytics.agents.worker import ToolChoice

    choice = ToolChoice.model_validate(
        {"done": False, "tool": "profile_table", "arguments": {"table": "orders"}}
    )
    assert choice.arguments == {"table": "orders"}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ("{}", {}),
        ("", {}),
        ("   ", {}),
        ({"a": 1}, {"a": 1}),
        (None, None),
    ],
)
def test_json_object_decoding(given: Any, expected: Any) -> None:
    assert decode_json_object(given) == expected


def test_undecodable_text_is_passed_through_for_the_model_to_reject() -> None:
    """Never turn a bad value into a plausible one.

    Returning `{}` for unparseable text would hand the engine an empty tool
    call that looks deliberate.
    """
    assert decode_json_object("not json at all") == "not json at all"
