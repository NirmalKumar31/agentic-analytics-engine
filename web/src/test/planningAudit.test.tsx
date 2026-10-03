import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlanningAudit } from "../components/PlanningAudit";
import type { RunPayload } from "../lib/types";

const run = (over: Partial<RunPayload> = {}): RunPayload => ({
  run_id: "r1", question: "What is total revenue by region?", dataset: { dataset_kind: "upload", source: "test", dataset_fingerprint: "x", tables: [], metrics_available: [] },
  report: null, findings: [], rejected: [], charts: [], results: {}, mcp_trace: [], tasks: [], metrics: { runtime_seconds: 1, provider: "scripted", analysis_tasks: 1, tasks_succeeded: 1, tasks_warned: 0, tasks_failed: 0, mcp_tool_calls: 1, findings_published: 1, findings_rejected: 0, charts: 0, llm_calls: 0 }, stopped_reason: "",
  query_contract: { operation: "sum", table: "uploaded_data", measure: "revenue", dimensions: ["region"], filters: [], ascending: false, confident: true, explanation: "ok", interpretation: "rule-based", contract_hash: "h" },
  question_coverage: { complete: true, required_components: ["operation", "measure", "dimensions"], applied_components: ["operation", "measure", "dimensions"], missing_components: [], rejection_codes: [], details: [] },
  events: [{ event_id: "e", seq: 1, type: "contract_resolved", at: 1, data: { route: "rules_exact", model_calls: 0 } }],
  timings: { planning_ms: 2, execution_ms: 3, verification_ms: 4, total_ms: 9 },
  ...over,
});

describe("PlanningAudit", () => {
  it("discloses the accepted contract and coverage without chain-of-thought", () => {
    render(
      <PlanningAudit
        run={run({ engine_version: "0.1.0", build_sha: "0123456789abcdef" })}
      />,
    );
    expect(screen.getByText("Planning audit")).toBeVisible();
    screen.getByText("Planning audit").click();
    expect(screen.getByText(/rules exact/i)).toBeVisible();
    expect(screen.getByText(/sum revenue by region/i)).toBeVisible();
    expect(screen.getByText(/every required component was applied/i)).toBeVisible();
    expect(screen.getByText("0.1.0")).toBeVisible();
    expect(screen.getByText("0123456789ab")).toBeVisible();
    expect(screen.queryByText(/system prompt|raw provider response/i)).toBeNull();
  });
});
