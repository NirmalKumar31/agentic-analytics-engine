"""The endpoint that lets a session owner settle a role inference cannot.

Everything here goes through the real upload path and the real
`infer_schema`, because the thing under test is whether a *genuinely*
ambiguous column can be confirmed, and a hand-written schema would let the
test assert that against a fixture the engine never produces.

The refusals matter more than the success. This endpoint accepts a
browser-supplied column name and changes how the engine aggregates it, so
every one of them is a boundary:

  - the capability cookie, or one session edits another's schema;
  - demo datasets, whose roles are governed definitions and not guesses;
  - active runs, because a run holds a schema snapshot for its execution;
  - the expected revision, or a stale tab confirms against a column list it
    is no longer showing;
  - the column, the role and the type, before anything is applied.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.api.runs import RunRegistry
from agentic_analytics.config import Settings

REPO = Path(__file__).resolve().parents[2]


def _settings(warehouse_dir: Path, tmp_path: Path, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "provider_mode": "fake",
        "live_analytics_enabled": True,
        "uploads_enabled": True,
        "data_dir": warehouse_dir.parent,
        "upload_dir": tmp_path / "uploads",
        "recordings_dir": REPO / "examples" / "recordings",
        "log_json": False,
    }
    return Settings(**(defaults | overrides))


def ambiguous_csv() -> bytes:
    """A dataset the real inference reports a close call on.

    `reading` holds 48 distinct integers across 400 rows: 12% uniqueness, inside
    the share ceiling and above the small-enumeration ceiling, which is the
    band where a code list and a genuine count are indistinguishable. Named neutrally on purpose. No production rule may key on a column name,
    and neither should the fixture that proves it, and the name has to
    avoid both hint lists, since `code`, `grade`, `tier` and `region` are all
    matched as substrings and would be classified by name before the
    cardinality rules were ever reached.
    """
    rows = ["place,amount,reading,when"]
    for i in range(400):
        region = ["north", "south", "east", "west"][i % 4]
        rows.append(f"{region},{100 + i * 7}.50,{18 + (i % 48)},2025-{(i % 12) + 1:02d}-15")
    return ("\n".join(rows) + "\n").encode()


def upload(client: TestClient, payload: bytes | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("rows.csv", io.BytesIO(payload or ambiguous_csv()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def field_named(summary: dict[str, Any], name: str) -> dict[str, Any]:
    for item in summary["fields"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"no field {name!r} in {[f['name'] for f in summary['fields']]}")


def roles_url(session_id: str) -> str:
    return f"/api/datasets/{session_id}/schema/roles"


@pytest.fixture
def client(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    settings = _settings(warehouse_dir, tmp_path)
    monkeypatch.setattr(type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir))
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def two_clients(
    warehouse_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, TestClient]]:
    """Two browsers against one deployment.

    Separate apps rather than two clients over one: the MCP session manager
    may only be started once per application instance, so a second lifespan
    on the same app raises before any assertion runs.
    """
    settings = _settings(warehouse_dir, tmp_path)
    monkeypatch.setattr(type(settings), "demo_warehouse_dir", property(lambda self: warehouse_dir))
    with TestClient(create_app(settings)) as first, TestClient(create_app(settings)) as second:
        yield first, second


# --------------------------------------------------------------- the shape


def test_the_real_inference_reports_the_close_call(client: TestClient) -> None:
    """If this stops holding, the rest of the file is testing nothing."""
    summary = upload(client)["summary"]
    code = field_named(summary, "reading")
    assert code["ambiguous"] is True
    assert code["role_source"] == "inferred"
    assert set(code["allowed_confirmed_roles"]) == {"measure", "dimension"}
    assert summary["schema_revision"] == 0
    assert summary["unresolved_ambiguity_count"] >= 1


def test_a_confirmation_is_applied_and_reported(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    code = field_named(summary, "reading")

    assert code["role"] == "dimension"
    assert code["inferred_role"] == "measure"
    assert code["role_source"] == "user_confirmed"
    # Still a close call: the values did not change.
    assert code["ambiguous"] is True
    assert summary["schema_revision"] == 1
    assert summary["confirmed_role_count"] == 1
    assert "reading" in summary["dimensions"]
    assert "reading" not in summary["measures"]


def test_a_reset_restores_inference(client: TestClient) -> None:
    session = upload(client)
    client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    response = client.patch(
        roles_url(session["session_id"]),
        json={"expected_revision": 1, "changes": [{"column": "reading", "action": "reset"}]},
    )
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    code = field_named(summary, "reading")
    assert code["role_source"] == "inferred"
    assert code["role"] == "measure"
    assert summary["schema_revision"] == 2
    assert summary["confirmed_role_count"] == 0


def test_a_no_op_batch_does_not_move_the_revision(client: TestClient) -> None:
    session = upload(client)
    client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    again = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 1,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert again.status_code == 200
    assert again.json()["summary"]["schema_revision"] == 1


# ----------------------------------------------------------- the refusals


def test_a_stale_revision_is_refused(client: TestClient) -> None:
    session = upload(client)
    client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    stale = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "reset"}],
        },
    )
    assert stale.status_code == 409
    assert stale.headers.get("X-Refusal-Reason") == "stale_revision"
    # And nothing was applied.
    current = client.get(f"/api/datasets/{session['session_id']}").json()["summary"]
    assert field_named(current, "reading")["role_source"] == "user_confirmed"


def test_a_demo_session_has_nothing_to_confirm(client: TestClient) -> None:
    demo = client.post("/api/datasets/demo").json()
    response = client.patch(
        roles_url(demo["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert response.status_code == 422
    assert response.headers.get("X-Refusal-Reason") == "not_an_upload"


def test_an_unknown_column_is_refused(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "not_a_column", "action": "confirm", "role": "measure"}],
        },
    )
    assert response.status_code == 422
    assert response.headers.get("X-Refusal-Reason") == "unknown_column"


def test_resetting_an_unknown_column_is_refused(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "not_a_column", "action": "reset"}],
        },
    )
    assert response.status_code == 422
    assert response.headers.get("X-Refusal-Reason") == "unknown_column"


def test_a_settled_column_is_refused(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "place", "action": "confirm", "role": "measure"}],
        },
    )
    assert response.status_code == 422
    assert response.headers.get("X-Refusal-Reason") in {"not_ambiguous", "unsupported_type"}


def test_an_unoffered_role_is_refused(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "time"}],
        },
    )
    # The closed enum refuses this before the handler sees it.
    assert response.status_code == 422


def test_a_batch_wrong_in_its_second_change_applies_neither(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [
                {"column": "reading", "action": "confirm", "role": "dimension"},
                {"column": "place", "action": "confirm", "role": "measure"},
            ],
        },
    )
    assert response.status_code == 422
    current = client.get(f"/api/datasets/{session['session_id']}").json()["summary"]
    assert field_named(current, "reading")["role_source"] == "inferred"
    assert current["schema_revision"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": 0, "changes": []},
        {"expected_revision": -1, "changes": [{"column": "reading", "action": "reset"}]},
        {"expected_revision": 0, "changes": [{"column": "reading", "action": "confirm"}]},
        {
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "reset", "role": "measure"}],
        },
        {
            "expected_revision": 0,
            "changes": [
                {"column": "reading", "action": "confirm", "role": "measure"},
                {"column": "reading", "action": "reset"},
            ],
        },
        {
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "reset"}],
            "note": "extra",
        },
        {
            "expected_revision": 0,
            "changes": [{"column": "c", "action": "reset"}] * 40,
        },
    ],
)
def test_a_malformed_batch_is_refused(client: TestClient, body: dict[str, Any]) -> None:
    session = upload(client)
    response = client.patch(roles_url(session["session_id"]), json=body)
    assert response.status_code == 422


# -------------------------------------------------------------- isolation


def test_another_session_cannot_confirm_this_one(
    two_clients: tuple[TestClient, TestClient],
) -> None:
    """Two browsers, two capabilities. The handle alone is not authority."""
    first, second = two_clients
    victim = upload(first)
    upload(second)  # gives `second` its own capability cookie

    response = second.patch(
        roles_url(victim["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert response.status_code == 404

    still = first.get(f"/api/datasets/{victim['session_id']}").json()["summary"]
    assert field_named(still, "reading")["role_source"] == "inferred"


def test_without_the_capability_the_handle_is_not_enough(
    two_clients: tuple[TestClient, TestClient],
) -> None:
    owner, stranger = two_clients
    session = upload(owner)
    response = stranger.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert response.status_code == 404


def test_a_confirmation_does_not_reach_another_session(
    two_clients: tuple[TestClient, TestClient],
) -> None:
    first, second = two_clients
    mine = upload(first)
    theirs = upload(second)
    first.patch(
        roles_url(mine["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    other = second.get(f"/api/datasets/{theirs['session_id']}").json()["summary"]
    assert field_named(other, "reading")["role_source"] == "inferred"
    assert other["schema_revision"] == 0


def test_the_capability_never_appears_in_the_response(client: TestClient) -> None:
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert "session_key" not in response.text
    assert "capability" not in response.text.lower()


# ------------------------------------------------------- active-run policy


def test_a_run_on_this_session_blocks_a_confirmation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run holds a schema snapshot for its whole execution.

    Changing the roles underneath it would leave evidence describing a
    schema the run never used, so the update is refused while work is in
    flight rather than racing it.

    The predicate is forced rather than a real analysis started: this test
    is about the endpoint honouring the policy, and the predicate itself is
    covered by the per-session test below.
    """
    monkeypatch.setattr(RunRegistry, "has_active_run_for_session", lambda self, session_id: True)
    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert response.status_code == 409
    assert response.headers.get("X-Refusal-Reason") == "active_run"


def test_a_run_on_another_session_does_not_block(client: TestClient) -> None:
    """Scoped per session on purpose.

    A global active-run count would let one visitor's analysis block another
    visitor's schema confirmation, which is both wrong and invisible to
    whoever is blocked.
    """
    registry = RunRegistry()
    registry.create(session_id="someone_else", question="q", mode="deterministic")
    assert registry.has_active_run_for_session("someone_else") is True
    assert registry.has_active_run_for_session("mine") is False

    session = upload(client)
    response = client.patch(
        roles_url(session["session_id"]),
        json={
            "expected_revision": 0,
            "changes": [{"column": "reading", "action": "confirm", "role": "dimension"}],
        },
    )
    assert response.status_code == 200
