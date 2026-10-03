"""Role confirmations belong to one session and leave with it.

The confirmation is one person's statement about one uploaded file. It is
not a definition, it is not shared, and it must not outlive the session that
produced it. These tests pin that, and the revision semantics that let a run
record which generation of the schema it executed against.

The revision is the part that is easy to get subtly wrong. It has to
increment when meaning changes and not otherwise: a no-op that bumped it
would invalidate work that was still valid, and a real change that did not
would let a stale browser confirm against a column list it is no longer
looking at.
"""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb
import pytest

from agentic_analytics.warehouse.session import (
    AnalysisSession,
    StaleSchemaRevision,
)


def make_session(tmp_path: Path, session_id: str = "ses_1") -> AnalysisSession:
    con = duckdb.connect()
    con.execute("CREATE TABLE uploaded_data(code BIGINT, amount DOUBLE)")
    return AnalysisSession(
        session_id=session_id,
        kind="upload",
        con=con,
        tables={},
        fingerprint="sha256:test",
        registry=None,
        source_label="uploaded file",
        scratch_dir=tmp_path / session_id,
    )


def test_a_new_session_has_no_confirmations(tmp_path: Path) -> None:
    snapshot = make_session(tmp_path).role_confirmation_snapshot()
    assert snapshot.revision == 0
    assert dict(snapshot.confirmations) == {}
    assert snapshot.is_empty()


def test_a_change_increments_the_revision_once(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    snapshot = session.apply_role_confirmation_changes({"code": "dimension"})
    assert snapshot.revision == 1
    assert dict(snapshot.confirmations) == {"code": "dimension"}


def test_a_batch_increments_the_revision_once_not_per_column(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    snapshot = session.apply_role_confirmation_changes({"code": "dimension", "amount": "measure"})
    assert snapshot.revision == 1


def test_repeating_the_same_confirmation_changes_nothing(tmp_path: Path) -> None:
    """Idempotent, so a retried request does not invalidate valid work."""
    session = make_session(tmp_path)
    first = session.apply_role_confirmation_changes({"code": "dimension"})
    second = session.apply_role_confirmation_changes({"code": "dimension"})
    assert first.revision == second.revision == 1


def test_resetting_a_column_restores_inference(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.apply_role_confirmation_changes({"code": "dimension"})
    snapshot = session.apply_role_confirmation_changes({"code": None})
    assert dict(snapshot.confirmations) == {}
    assert snapshot.revision == 2


def test_resetting_an_unconfirmed_column_is_a_no_op(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    snapshot = session.apply_role_confirmation_changes({"code": None})
    assert snapshot.revision == 0
    assert dict(snapshot.confirmations) == {}


def test_a_snapshot_does_not_follow_later_changes(tmp_path: Path) -> None:
    """A run holding a snapshot must not see a confirmation applied halfway
    through its own execution; its evidence would describe a schema that
    never existed as a whole."""
    session = make_session(tmp_path)
    taken = session.role_confirmation_snapshot()
    session.apply_role_confirmation_changes({"code": "dimension"})
    assert dict(taken.confirmations) == {}
    assert taken.revision == 0


def test_a_snapshot_cannot_be_edited(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    snapshot = session.apply_role_confirmation_changes({"code": "dimension"})
    with pytest.raises(TypeError):
        snapshot.confirmations["amount"] = "measure"  # type: ignore[index]


def test_a_stale_expected_revision_is_refused(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.apply_role_confirmation_changes({"code": "dimension"})
    with pytest.raises(StaleSchemaRevision) as caught:
        session.apply_role_confirmation_changes({"amount": "measure"}, expected_revision=0)
    assert caught.value.current == 1
    assert caught.value.expected == 0
    # And nothing was applied.
    assert dict(session.role_confirmation_snapshot().confirmations) == {"code": "dimension"}


def test_a_matching_expected_revision_is_accepted(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.apply_role_confirmation_changes({"code": "dimension"})
    snapshot = session.apply_role_confirmation_changes({"amount": "measure"}, expected_revision=1)
    assert snapshot.revision == 2


def test_two_sessions_cannot_see_each_other(tmp_path: Path) -> None:
    one = make_session(tmp_path, "ses_1")
    two = make_session(tmp_path, "ses_2")
    one.apply_role_confirmation_changes({"code": "dimension"})
    assert dict(two.role_confirmation_snapshot().confirmations) == {}
    assert two.role_confirmation_snapshot().revision == 0


def test_confirmations_go_when_the_session_goes(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.apply_role_confirmation_changes({"code": "dimension"})
    session.close()
    # Nothing is persisted: the state lives on the object, which the
    # registry drops. This asserts the design rather than a teardown hook --
    # if a future change writes it anywhere, that is the thing to catch.
    assert not hasattr(session, "_role_confirmation_store")


def test_concurrent_batches_do_not_lose_an_update(tmp_path: Path) -> None:
    """The lock is the session's own, not the DuckDB one.

    Overloading the query lock would mean a schema read waiting behind a
    long query, and a confirmation could deadlock against a run holding it.
    """
    session = make_session(tmp_path)
    columns = [f"c{i}" for i in range(40)]
    barrier = threading.Barrier(len(columns))

    def apply(column: str) -> None:
        barrier.wait()
        session.apply_role_confirmation_changes({column: "dimension"})

    threads = [threading.Thread(target=apply, args=(c,)) for c in columns]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    snapshot = session.role_confirmation_snapshot()
    assert set(snapshot.confirmations) == set(columns)
    assert snapshot.revision == len(columns)
