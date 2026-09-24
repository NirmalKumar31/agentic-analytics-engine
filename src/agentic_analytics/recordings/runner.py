"""Producing the published demo recordings.

Each demo question is run for real against the generated warehouse with the
scripted provider, and the result is validated before it is written. Nothing
about the trace, the numbers or the events is authored by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_analytics.config import get_settings
from agentic_analytics.graph.runner import run_analysis
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.recordings.record import build_recording, write_recording
from agentic_analytics.warehouse.session import SessionManager, open_demo_session

DEMOS: list[dict[str, str | int]] = [
    {
        "recording_id": "margin-q3",
        "order": 1,
        "question": "Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        "title": "Revenue up, margin down in Q3",
        "demonstrates": (
            "Parallel time-series, discount and product-mix analysis, with the "
            "quarter named in the question driving the comparison window."
        ),
    },
    {
        "recording_id": "returns-segments",
        "order": 2,
        "question": "Which customer segments are driving the increase in return rate?",
        "title": "Return rate by customer segment",
        "demonstrates": (
            "Segmentation across several metrics, with a chi-square test of "
            "independence computed by the tool rather than asserted."
        ),
    },
    {
        "recording_id": "shipping-repeat",
        "order": 3,
        "question": "Do shipping delays appear to affect repeat purchasing?",
        "title": "Shipping delay and repeat purchase",
        "demonstrates": (
            "A joined cohort comparison, a two-proportion z-test, and a causal "
            "interpretation that the verifier rejects."
        ),
    },
]


async def record_demos(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Run every demo question and write the recordings that pass."""
    cfg = get_settings()
    manager = SessionManager()
    server = build_server(manager, cfg)
    written: list[tuple[Path, dict[str, Any]]] = []
    try:
        for demo in DEMOS:
            session = manager.add(open_demo_session(cfg.demo_warehouse_dir))
            result = await run_analysis(str(demo["question"]), session, server, settings=cfg)
            recording = build_recording(
                result,
                recording_id=str(demo["recording_id"]),
                title=str(demo["title"]),
                demonstrates=str(demo["demonstrates"]),
                order=int(demo["order"]),
            )
            path = write_recording(recording, directory)
            written.append((path, result.metrics))
            manager.drop(session.session_id)
    finally:
        manager.close_all()
    return written
