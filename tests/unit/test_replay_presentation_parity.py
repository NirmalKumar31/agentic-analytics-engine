"""A replayed report reads exactly as the run that produced it did.

A recording is replayed through the **live** report components, so the
question is not "is the snapshot well formed" but "does the snapshot say
the same thing the live formatter would". Those came apart in public: the
recordings carried no presentation snapshot at all, so a replayed report
fell back to the engine's own sentences and published `return_rate` and
`2025-01-01` to the one audience that cannot upload a file of their own.

Parity is checked in the direction that can actually be checked without a
second engine run: every figure the snapshot has already formatted must be
what `display_value` produces from the raw cell and the field beside it. If
the live formatter changes and the artefacts are not regenerated, this
fails and says which recording.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.presentation.fields import display_value
from agentic_analytics.presentation.schemas import DisplayField

RECORDINGS = Path(__file__).resolve().parents[2] / "examples" / "recordings"


def _recordings() -> list[tuple[str, dict[str, Any]]]:
    return [(path.stem, json.loads(path.read_text())) for path in sorted(RECORDINGS.glob("*.json"))]


IDS = [name for name, _ in _recordings()]


def _fields(presentation: dict[str, Any]) -> dict[str, DisplayField]:
    declared = (
        (presentation.get("table") or {}).get("display_fields")
        or presentation.get("display_fields")
        or []
    )
    return {entry["source_name"]: DisplayField(**entry) for entry in declared}


@pytest.mark.parametrize(("name", "recording"), _recordings(), ids=IDS)
def test_every_recording_carries_a_presentation_snapshot(
    name: str, recording: dict[str, Any]
) -> None:
    """Without it, the replay silently falls back to the engine's own prose.

    Silently is the operative word: the report still renders, so nothing
    fails. It just answers in `return_rate fell from 8.51% in 2025-01-01`
    instead of the product's own sentence, to the one audience that has no
    file of their own to upload.
    """
    assert "presentation" in recording, f"{name} predates the presentation snapshot"
    assert recording["presentation"], (
        f"{name} carries a null presentation; a replayed report would render "
        "the engine's finding text rather than the product's answer"
    )


@pytest.mark.parametrize(("name", "recording"), _recordings(), ids=IDS)
def test_every_stored_figure_matches_the_cell_it_cites(
    name: str, recording: dict[str, Any]
) -> None:
    """A highlight restates a cell, and the restatement has to *be* the cell.

    This is the parity assertion that bites. The snapshot was written by
    one version of the formatter and is read by whatever version is
    running, so if the two disagree a replayed report is published with
    figures the live code would have written differently, which is
    exactly the state the recordings were in, at a coarser grain.

    The field is resolved from the column the highlight cites, so a
    recording that cites a column it does not declare fails here rather
    than being skipped.
    """
    presentation = recording["presentation"]
    results = recording["results"]
    fields = _fields(presentation)
    checked = 0
    for highlight in presentation.get("highlights") or []:
        cells = highlight.get("evidence_cells") or []
        assert cells, (
            f"{name}: highlight {highlight['highlight_id']!r} cites no cell, so "
            "nothing can confirm the figure it states"
        )
        cell = cells[0]
        snapshot = results[cell["result_id"]]
        raw = snapshot["rows"][cell["row"]][snapshot["columns"].index(cell["column"])]
        field = fields.get(cell["column"])
        assert field is not None, (
            f"{name}: highlight {highlight['highlight_id']!r} cites column "
            f"{cell['column']!r}, which the presentation does not describe"
        )
        assert display_value(raw, field) == highlight["value"]["formatted_value"], (
            f"{name}: highlight {highlight['highlight_id']!r} states "
            f"{highlight['value']['formatted_value']!r} for a cell the live "
            f"formatter reads as {display_value(raw, field)!r}"
        )
        checked += 1
    assert checked > 0, f"{name} published no highlight to check"


FORBIDDEN = {
    "a Python None": re.compile(r"\bNone\b"),
    "a list repr": re.compile(r"[\[\]]"),
    "a stored instant": re.compile(r"\d{4}-\d{2}-\d{2}T"),
}


@pytest.mark.parametrize(("name", "recording"), _recordings(), ids=IDS)
def test_no_reader_facing_string_in_the_snapshot_is_an_internal_form(
    name: str, recording: dict[str, Any]
) -> None:
    """The headline, the scope line, the labels and every formatted figure.

    Deliberately not the whole snapshot: `source_name`, the evidence cells
    and the provenance refs carry engine identifiers on purpose, and that
    is where a reader who asked how a figure was reached needs them.
    """
    presentation = recording["presentation"]
    reader_strings: list[tuple[str, str]] = [
        ("headline", presentation.get("headline") or ""),
        ("secondary_summary", presentation.get("secondary_summary") or ""),
    ]
    for index, entry in enumerate(presentation.get("scope", {}).get("filters") or []):
        reader_strings.append((f"scope.filters[{index}]", str(entry)))
    period = presentation.get("scope", {}).get("period")
    if period:
        reader_strings.append(("scope.period", str(period)))
    for highlight in presentation.get("highlights") or []:
        reader_strings.append((f"highlight {highlight['highlight_id']}", highlight["label"]))
        reader_strings.append(
            (f"highlight {highlight['highlight_id']} value", highlight["value"]["formatted_value"])
        )
    for entry in _fields(presentation).values():
        reader_strings.append((f"label for {entry.source_name}", entry.display_label))
    for caveat in presentation.get("caveats") or []:
        reader_strings.append((caveat.get("code", "caveat"), caveat.get("message", "")))

    for where, text in reader_strings:
        for description, pattern in FORBIDDEN.items():
            assert not pattern.search(text), f"{name} at {where}: {text!r} contains {description}"


@pytest.mark.parametrize(("name", "recording"), _recordings(), ids=IDS)
def test_a_time_column_declares_its_grain(name: str, recording: dict[str, Any]) -> None:
    """Without the grain the table cannot write the period the headline wrote."""
    for field in _fields(recording["presentation"]).values():
        if field.semantic_kind.value != "time":
            continue
        assert field.time_grain, (
            f"{name}: {field.source_name} is a time column with no declared grain, "
            "so a reader surface has nothing to format it with"
        )
