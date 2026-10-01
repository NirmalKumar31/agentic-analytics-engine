/**
 * What each mode actually did, as its own lane.
 *
 * The previous flow diagram rendered one event stream above both panes, and
 * that stream was the deterministic run's. In Compare Both the AI lane was
 * therefore invisible: a run that spent one model call translating the
 * question and none computing the answer looked identical to one where the
 * model had produced the result. MCP was the only substantive stage on
 * screen, which is true of the arithmetic and misleading about the whole.
 *
 * Each lane is derived from its own run's events and timings.
 *
 * There are two execution paths and the lane names whichever one ran. An
 * uploaded dataset is answered from a typed contract: a resolver produces
 * one, it is validated, and only then does anything execute. The bundled
 * demo dataset is answered from the metric registry by planning agents,
 * which never produce an upload contract at all. Describing both with the
 * contract path's stage names made a working demo run report "Rule
 * resolver: not reached" and "Contract validation: not accepted" -- two
 * false statements about a run that succeeded. A stage is only shown as not
 * reached when it was on the path and did not happen.
 */

import type { RunPayload } from "../lib/types";
import { runState } from "../lib/runState";

type StageState = "done" | "active" | "failed" | "skipped" | "idle";

interface Stage {
  key: string;
  label: string;
  detail: string;
  state: StageState;
}

function eventsOfType(run: RunPayload, type: string) {
  return (run.events ?? []).filter((event) => event.type === type);
}

function contractEvent(run: RunPayload) {
  return eventsOfType(run, "contract_resolved")[0];
}

function ms(value: number | undefined | null): string {
  if (value == null) return "";
  return value >= 1000
    ? `${(value / 1000).toFixed(1)}s`
    : `${Math.round(value)}ms`;
}

/**
 * Which of the two execution paths this run took.
 *
 * The contract path is identified by evidence that a contract existed, not
 * by the absence of something else, so a run that fails before planning is
 * still described by the path it was on.
 */
function tookContractPath(run: RunPayload): boolean {
  return Boolean(contractEvent(run) || run.query_contract);
}

/** The planner stage, which is the only one the two modes do differently. */
function plannerStage(run: RunPayload, mode: "deterministic" | "ai"): Stage {
  const event = contractEvent(run);
  const data = (event?.data ?? {}) as Record<string, unknown>;
  const calls = Number(data.model_calls ?? 0);
  const duration = run.timings?.planning_ms;
  const fallback = Boolean(run.planner_fallback || data.fallback);

  if (mode === "deterministic") {
    return {
      key: "planner",
      label: "Rule resolver",
      detail: event
        ? `no model calls${duration != null ? ` · ${ms(duration)}` : ""}`
        : "not reached",
      state: event ? "done" : "idle",
    };
  }
  return {
    key: "planner",
    label: "Cloud semantic planner",
    detail: !event
      ? "not reached"
      : fallback
        ? `plan unusable · fell back to the rules contract${
            duration != null ? ` · ${ms(duration)}` : ""
          }`
        : `${calls} model call${calls === 1 ? "" : "s"}${
            duration != null ? ` · ${ms(duration)}` : ""
          }`,
    state: !event ? "idle" : fallback ? "failed" : "done",
  };
}

/**
 * The first two stages of the registry path: understanding the question,
 * then planning the work. Both modes run these; the AI mode uses a model to
 * do it and the deterministic mode a scripted stand-in, which is what the
 * lane says rather than inventing a contract that was never built.
 */
function registryStages(
  run: RunPayload,
  mode: "deterministic" | "ai",
): Stage[] {
  const analysed = eventsOfType(run, "question_analyzed")[0];
  const analysedData = (analysed?.data ?? {}) as Record<string, unknown>;
  const planned = eventsOfType(run, "plan_generated")[0];
  const plannedData = (planned?.data ?? {}) as Record<string, unknown>;
  const tasks = Number(plannedData.task_count ?? 0);
  const rounds = eventsOfType(run, "followup_round_started").length;
  const metrics = Array.isArray(analysedData.target_metrics)
    ? (analysedData.target_metrics as unknown[]).map(String)
    : [];
  const dimensions = Array.isArray(analysedData.dimensions)
    ? (analysedData.dimensions as unknown[]).map(String)
    : [];

  const understanding = [
    analysedData.analysis_type ? String(analysedData.analysis_type) : undefined,
    metrics.length > 0 ? metrics.slice(0, 3).join(", ") : undefined,
    dimensions.length > 0 ? `by ${dimensions.slice(0, 2).join(" then ")}` : undefined,
  ]
    .filter(Boolean)
    .join(" · ");

  return [
    {
      key: "planner",
      label: mode === "ai" ? "Question analyst" : "Scripted analyst",
      detail: analysed ? understanding || "question understood" : "not reached",
      state: analysed ? "done" : "idle",
    },
    {
      key: "contract",
      label: "Analysis plan",
      detail: planned
        ? `${tasks} task${tasks === 1 ? "" : "s"}${
            rounds > 0 ? ` · ${rounds} follow-up round${rounds === 1 ? "" : "s"}` : ""
          }${run.timings?.planning_ms != null ? ` · ${ms(run.timings.planning_ms)}` : ""}`
        : "not reached",
      state: planned ? "done" : "idle",
    },
  ];
}

export function laneStages(
  run: RunPayload | null | undefined,
  mode: "deterministic" | "ai",
): Stage[] {
  if (!run) {
    return [
      {
        key: "planner",
        label: mode === "ai" ? "Cloud semantic planner" : "Rule resolver",
        detail: "not started",
        state: "idle",
      },
      {
        key: "contract",
        label: "Contract validation",
        detail: "",
        state: "idle",
      },
      { key: "compute", label: "DuckDB via MCP", detail: "", state: "idle" },
      { key: "verify", label: "Verification", detail: "", state: "idle" },
      { key: "report", label: "Report", detail: "", state: "idle" },
    ];
  }

  const state = runState(run);
  const contract = run.query_contract;
  const coverage = run.question_coverage;
  const calls = eventsOfType(run, "mcp_tool_called").length;
  const rows = Object.values(run.results ?? {}).reduce(
    (total, snapshot) => total + (snapshot.row_count ?? 0),
    0,
  );
  const published = (run.findings ?? []).length;

  const contractDetail = contract?.confident
    ? [
        contract.operation,
        contract.measure ?? undefined,
        (contract.dimensions ?? []).length > 0
          ? `by ${(contract.dimensions ?? []).join(" then ")}`
          : undefined,
        contract.time_grain ? `${contract.time_grain}ly` : undefined,
      ]
        .filter(Boolean)
        .join(" · ")
    : "not accepted";

  const coverageFailed = coverage ? !coverage.complete : false;

  const head: Stage[] = tookContractPath(run)
    ? [
        plannerStage(run, mode),
        {
          key: "contract",
          label: "Contract validation",
          detail: coverageFailed
            ? `refused · ${coverage?.rejection_codes.join(", ")}`
            : contractDetail,
          state: !contract ? "idle" : coverageFailed ? "failed" : "done",
        },
      ]
    : registryStages(run, mode);

  return [
    ...head,
    {
      key: "compute",
      label: "DuckDB via MCP",
      detail:
        calls === 0
          ? state.state === "refused"
            ? "not run · the request was refused first"
            : "not run"
          : `${calls} quer${calls === 1 ? "y" : "ies"} · ${rows.toLocaleString()} result row${
              rows === 1 ? "" : "s"
            }${run.timings?.execution_ms != null ? ` · ${ms(run.timings.execution_ms)}` : ""}`,
      state:
        calls === 0 ? (state.state === "refused" ? "skipped" : "idle") : "done",
    },
    {
      key: "verify",
      label: "Verification",
      detail:
        calls === 0
          ? ""
          : `${published} published · ${(run.rejected ?? []).length} withheld${
              run.timings?.verification_ms != null
                ? ` · ${ms(run.timings.verification_ms)}`
                : ""
            }`,
      state: calls === 0 ? "skipped" : published > 0 ? "done" : "failed",
    },
    {
      key: "report",
      label: "Report",
      detail:
        state.state === "completed_verified"
          ? "complete"
          : state.label.toLowerCase(),
      state:
        state.state === "completed_verified"
          ? "done"
          : state.state === "running"
            ? "active"
            : "failed",
    },
  ];
}

interface Props {
  run: RunPayload | null | undefined;
  mode: "deterministic" | "ai";
  title: string;
  /**
   * False when this lane stands alone rather than beside its counterpart.
   * The closing note compares the two modes, which is a claim about a
   * comparison that is not on screen in a single-mode run.
   */
  compared?: boolean;
}

export function ExecutionLane({
  run,
  mode,
  title,
  compared = true,
}: Props) {
  const stages = laneStages(run, mode);
  return (
    <section
      className="lane"
      aria-label={`${title} execution`}
      data-mode={mode}
    >
      <h3 className="lane-title">{title}</h3>
      <ol className="lane-stages">
        {stages.map((stage) => (
          <li
            key={stage.key}
            className={`lane-stage state-${stage.state}`}
            data-stage={stage.key}
          >
            <span className="lane-stage-label">{stage.label}</span>
            {stage.detail ? (
              <span className="lane-stage-detail small dim">
                {stage.detail}
              </span>
            ) : null}
          </li>
        ))}
      </ol>
      <p className="small dim lane-note">
        {compared
          ? "Both lanes compute with DuckDB through MCP. The planner differs; the arithmetic does not."
          : "Every figure above was computed by DuckDB through MCP and checked before publication. No model calculated a result."}
      </p>
    </section>
  );
}
