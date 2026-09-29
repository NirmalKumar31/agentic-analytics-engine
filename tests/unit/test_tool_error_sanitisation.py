"""A tool failure must not carry the session capability.

Found by reproducing a real run. Every MCP call carries `session_id` and
`session_key`, injected by the client. When a call fails Pydantic's
validation, the error echoes the arguments back:

    1 validation error for aggregate_for_questionArguments
    table Field required [type=missing,
    input_value={'question': 'Breakdown o...mgiKsrKWFzNi6ndWA24v-8'}, ...]

That tail is the capability. The warehouse run produced thirty-six of these.
It mattered more after failures started being fed back to the worker: the
string is stored in the evaluation artifact, shown in the run trace, and put
into the next prompt.

Replacing the exact key is not enough on its own, because Pydantic truncates
the middle of the repr and leaves only a fragment. Removing the echoed input
is what closes it.
"""

from __future__ import annotations

from agentic_analytics.mcp_layer.client import (
    MAX_TOOL_ERROR_CHARS,
    _sanitize_tool_error,
)

SESSION_ID = "ses_H6vg3RMCg0HGrNgt"
SESSION_KEY = "AbCd_1234-EFgh5678ijKLmgiKsrKWFzNi6ndWA24v-8"


def _clean(message: str) -> str:
    return _sanitize_tool_error(message, session_id=SESSION_ID, session_key=SESSION_KEY)


def test_the_real_message_from_the_warehouse_run_is_stripped() -> None:
    """Verbatim, including the truncation that left only a key fragment."""
    message = (
        "1 validation error for aggregate_for_questionArguments table Field required "
        "[type=missing, input_value={'question': 'Breakdown o...mgiKsrKWFzNi6ndWA24v-8'}, "
        "input_type=dict] For further information visit https://errors.pydantic.dev"
    )
    cleaned = _clean(message)

    assert "mgiKsrKWFzNi6ndWA24v-8" not in cleaned
    assert "input_value=[omitted]" in cleaned
    # The part a model can act on survives.
    assert "table Field required" in cleaned
    assert "aggregate_for_questionArguments" in cleaned


def test_a_key_spelled_out_in_full_is_replaced() -> None:
    """Defence in depth for a layer that does not use `input_value`."""
    cleaned = _clean(f"call rejected for session_key={SESSION_KEY}")
    assert SESSION_KEY not in cleaned
    assert "[redacted]" in cleaned


def test_the_session_handle_is_replaced_too() -> None:
    cleaned = _clean(f"unknown or expired session {SESSION_ID!r}")
    assert SESSION_ID not in cleaned
    assert "[redacted]" in cleaned


def test_an_ordinary_tool_error_is_left_readable() -> None:
    """Sanitising must not destroy the message the worker needs."""
    cleaned = _clean("metric 'sales' does not exist; available: revenue, orders")
    assert cleaned == "metric 'sales' does not exist; available: revenue, orders"


def test_a_long_error_is_bounded() -> None:
    cleaned = _clean("x" * 5000)
    assert len(cleaned) == MAX_TOOL_ERROR_CHARS


def test_whitespace_is_collapsed_so_one_failure_is_one_line() -> None:
    assert _clean("a\n\n   b\tc") == "a b c"


def test_an_empty_key_does_not_redact_everything() -> None:
    """A provider with no capability configured must not blank the message."""
    assert _sanitize_tool_error("plain failure", session_id="", session_key="") == "plain failure"


def test_multiple_input_values_are_all_removed() -> None:
    message = (
        "2 validation errors input_value={'session_key': 'AbCd_1234'}, input_type=dict "
        "and input_value={'session_key': 'AbCd_1234'}, input_type=dict"
    )
    cleaned = _clean(message)
    assert "AbCd_1234" not in cleaned
    assert cleaned.count("input_value=[omitted]") == 2
