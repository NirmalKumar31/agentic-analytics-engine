"""Confirming a role the data cannot settle.

`infer_schema` reports a close call when a numeric column sits in the band
where a code list and a genuine count look the same: 45 integers recurring
across 400 rows is as consistent with a store number as with a quantity.
Nothing in the values decides it, and the engine has been wrong about it in
both directions.

The missing fact is one only the person who uploaded the file has. These
tests cover the layer that lets them supply it, and the distinctions that
make it honest rather than merely convenient:

  - inference stays untouched, so the historical classification survives;
  - the column stays ambiguous, because the values did not change;
  - confirming is scoped to one session and proves nothing beyond it;
  - a confirmed measure is not thereby a summable one.
"""

from __future__ import annotations

import pytest

from agentic_analytics.analytics.semantic import (
    InferredField,
    InferredSchema,
    RoleConfirmationError,
    apply_role_confirmations,
    validate_role_confirmations,
)


def numeric_close_call(name: str = "code", role: str = "measure") -> InferredField:
    return InferredField(
        name=name,
        data_type="BIGINT",
        role=role,  # type: ignore[arg-type]
        null_pct=0.0,
        distinct_count=45,
        reason="integer with 45 values recurring across about 9 rows",
        additive="weak",
        ambiguous=True,
    )


def settled(name: str = "amount") -> InferredField:
    return InferredField(
        name=name,
        data_type="DOUBLE",
        role="measure",
        null_pct=0.0,
        distinct_count=390,
        reason="a fractional quantity",
        additive="strong",
        ambiguous=False,
    )


def schema(*fields: InferredField) -> InferredSchema:
    return InferredSchema(table="uploaded_data", row_count=400, fields=list(fields))


# ---------------------------------------------------------------- the shape


def test_inference_is_not_mutated() -> None:
    """The caller's schema is evidence of what classification decided."""
    original = schema(numeric_close_call())
    apply_role_confirmations(original, {"code": "dimension"})
    assert original.fields[0].role == "measure"
    assert original.fields[0].role_source == "inferred"
    assert original.schema_revision == 0


def test_the_result_is_a_new_schema() -> None:
    original = schema(numeric_close_call())
    out = apply_role_confirmations(original, {"code": "dimension"})
    assert out is not original
    assert out.fields[0] is not original.fields[0]


def test_column_order_is_preserved() -> None:
    original = schema(settled("a"), numeric_close_call("b"), settled("c"))
    out = apply_role_confirmations(original, {"b": "dimension"})
    assert [f.name for f in out.fields] == ["a", "b", "c"]


def test_the_inferred_role_survives_the_confirmation() -> None:
    # Without this a confirmed column is indistinguishable from one the
    # engine classified correctly on its own, and the audit cannot say which
    # happened.
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "dimension"})
    assert out.fields[0].role == "dimension"
    assert out.fields[0].inferred_role == "measure"
    assert out.fields[0].role_source == "user_confirmed"


def test_the_column_is_still_a_close_call() -> None:
    """A confirmation supplies a fact; it does not change the data."""
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "dimension"})
    assert out.fields[0].ambiguous is True
    assert out.fields[0].reason == "integer with 45 values recurring across about 9 rows"


def test_confirming_the_role_inference_chose_still_changes_its_warrant() -> None:
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "measure"})
    assert out.fields[0].role == "measure"
    assert out.fields[0].role_source == "user_confirmed"
    assert out.fields[0].inferred_role == "measure"


# ------------------------------------------------------------ derived lists


def test_derived_lists_follow_the_effective_role() -> None:
    out = apply_role_confirmations(schema(numeric_close_call(), settled()), {"code": "dimension"})
    assert out.dimensions == ["code"]
    assert out.measures == ["amount"]
    assert "code" not in out.measures


def test_a_confirmed_dimension_becomes_aggregatable_if_named() -> None:
    # A numeric column read as a dimension can still be totalled when the
    # question names it. The list is derived, so it has to follow.
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "dimension"})
    assert out.aggregatable_if_named == ["code"]


def test_counts_distinguish_confirmed_from_unresolved() -> None:
    original = schema(numeric_close_call("a"), numeric_close_call("b"))
    assert original.unresolved_ambiguity_count == 2
    assert original.confirmed_role_count == 0

    out = apply_role_confirmations(original, {"a": "dimension"})
    assert out.confirmed_role_count == 1
    # `b` is still unsettled; `a` is settled but still ambiguous.
    assert out.unresolved_ambiguity_count == 1


# -------------------------------------------------------------- additivity


def test_confirming_a_measure_does_not_claim_it_can_be_summed() -> None:
    """Role and additivity are different questions.

    Carrying a `weak` or `strong` across from a different classification
    would let a suggestion propose totalling a column on the strength of a
    judgement nobody made.
    """
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "measure"})
    # It was classified `measure` already, so nothing changed role-wise.
    assert out.fields[0].additive == "weak"

    as_dim = apply_role_confirmations(schema(numeric_close_call()), {"code": "dimension"})
    became = apply_role_confirmations(
        schema(numeric_close_call(role="dimension")), {"code": "measure"}
    )
    assert as_dim.fields[0].additive == "weak"
    assert became.fields[0].additive == "unknown"


# -------------------------------------------------------------- refusals


def test_an_unknown_column_is_refused() -> None:
    with pytest.raises(RoleConfirmationError) as caught:
        apply_role_confirmations(schema(numeric_close_call()), {"nope": "dimension"})
    assert caught.value.reason == "unknown_column"


def test_a_settled_column_is_refused() -> None:
    # Offering a menu for a column the data classifies invites someone to
    # "correct" an answer that was never in doubt.
    with pytest.raises(RoleConfirmationError) as caught:
        apply_role_confirmations(schema(settled()), {"amount": "dimension"})
    assert caught.value.reason == "not_ambiguous"


@pytest.mark.parametrize("role", ["time", "identifier", "ignored", "measurement", ""])
def test_an_unoffered_role_is_refused(role: str) -> None:
    with pytest.raises(RoleConfirmationError) as caught:
        apply_role_confirmations(schema(numeric_close_call()), {"code": role})
    assert caught.value.reason == "unsupported_role"


def test_a_non_numeric_close_call_is_refused() -> None:
    text = InferredField(
        name="label",
        data_type="VARCHAR",
        role="dimension",
        null_pct=0.0,
        distinct_count=45,
        reason="text",
        ambiguous=True,
    )
    with pytest.raises(RoleConfirmationError) as caught:
        apply_role_confirmations(schema(text), {"label": "measure"})
    assert caught.value.reason == "unsupported_type"


def test_a_batch_is_refused_whole() -> None:
    """A request wrong in its second change must not leave the first applied."""
    original = schema(numeric_close_call("a"), settled("b"))
    with pytest.raises(RoleConfirmationError):
        apply_role_confirmations(original, {"a": "dimension", "b": "dimension"})
    assert original.fields[0].role == "measure"


def test_validation_is_available_without_applying() -> None:
    # The API validates the whole batch before touching session state.
    validate_role_confirmations(schema(numeric_close_call()), {"code": "dimension"})
    with pytest.raises(RoleConfirmationError):
        validate_role_confirmations(schema(settled()), {"amount": "measure"})


# ------------------------------------------------------------ serialisation


def test_the_payload_carries_both_roles_and_the_source() -> None:
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "dimension"}, revision=3)
    field_payload = out.as_dict()["fields"][0]
    assert field_payload["role"] == "dimension"
    assert field_payload["inferred_role"] == "measure"
    assert field_payload["role_source"] == "user_confirmed"
    assert field_payload["ambiguous"] is True
    assert field_payload["allowed_confirmed_roles"] == ["measure", "dimension"]

    payload = out.as_dict()
    assert payload["schema_revision"] == 3
    assert payload["confirmed_role_count"] == 1
    assert payload["unresolved_ambiguity_count"] == 0
    # The schema as a whole is still this module's classification.
    assert payload["status"] == "inferred"


def test_a_settled_field_offers_no_choices() -> None:
    payload = schema(settled()).as_dict()["fields"][0]
    assert payload["allowed_confirmed_roles"] == []
    assert payload["role_source"] == "inferred"
    assert payload["inferred_role"] == "measure"


def test_serialisation_does_not_need_a_session() -> None:
    """`as_dict` stays pure: confirmations are applied before it, never by it."""
    out = apply_role_confirmations(schema(numeric_close_call()), {"code": "dimension"})
    assert out.as_dict()["dimensions"] == ["code"]
