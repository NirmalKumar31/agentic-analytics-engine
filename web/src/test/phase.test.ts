import { describe, expect, it } from "vitest";

import { isIdle, phaseOf, showsReport, type PhaseInputs } from "../lib/phase";
import type { RunPayload, ServerConfig, SessionPayload } from "../lib/types";

const config = {} as ServerConfig;
const session = {} as SessionPayload;

function inputs(over: Partial<PhaseInputs> = {}): PhaseInputs {
  return {
    config,
    configError: null,
    session: null,
    replay: null,
    runId: null,
    run: null,
    busy: false,
    error: null,
    ...over,
  };
}

function run(over: Partial<RunPayload> = {}): RunPayload {
  return { events: [], ...over } as RunPayload;
}

describe("phaseOf", () => {
  it("is booting until the server says what it can do", () => {
    expect(phaseOf(inputs({ config: null }))).toBe("booting");
  });

  it("reports a configuration failure as failed", () => {
    expect(phaseOf(inputs({ config: null, configError: "nope" }))).toBe(
      "failed",
    );
  });

  it("asks for a dataset when there is none", () => {
    expect(phaseOf(inputs())).toBe("choose_dataset");
  });

  it("keeps the upload control in view after a failed upload", () => {
    // Reporting `failed` here would replace the thing the visitor needs
    // to see -- the upload control -- with a dead end.
    expect(phaseOf(inputs({ error: "that file was not readable" }))).toBe(
      "choose_dataset",
    );
  });

  it("is profiling while a file is being read", () => {
    expect(phaseOf(inputs({ busy: true }))).toBe("profiling");
  });

  it("is ready to ask once a dataset is open", () => {
    expect(phaseOf(inputs({ session }))).toBe("ready_to_ask");
  });

  it("is ready to ask for a recorded run too", () => {
    expect(phaseOf(inputs({ replay: {} }))).toBe("ready_to_ask");
  });

  describe("while a run is in flight", () => {
    const ev = (...types: string[]) => types.map((type) => ({ type }));

    it("starts at routing, before the engine has said what it will compute", () => {
      expect(phaseOf(inputs({ session, runId: "r1", events: [] }))).toBe(
        "routing",
      );
    });

    it("advances to executing when a query is dispatched", () => {
      expect(
        phaseOf(
          inputs({
            session,
            runId: "r1",
            events: ev("contract_resolved", "mcp_tool_called"),
          }),
        ),
      ).toBe("executing");
    });

    it("advances to verifying when findings are being checked", () => {
      expect(
        phaseOf(
          inputs({
            session,
            runId: "r1",
            events: ev("mcp_tool_called", "finding_proposed"),
          }),
        ),
      ).toBe("verifying");
    });

    it("advances to presenting when the report starts", () => {
      expect(
        phaseOf(
          inputs({
            session,
            runId: "r1",
            events: ev("mcp_tool_called", "finding_verified", "report_started"),
          }),
        ),
      ).toBe("presenting");
    });

    it("reports the furthest stage reached, not the latest event", () => {
      // A trailing MCP call after verification began must not pull the
      // phase backwards: the reader would see the report regress.
      expect(
        phaseOf(
          inputs({
            session,
            runId: "r1",
            events: ev("finding_verified", "mcp_tool_called"),
          }),
        ),
      ).toBe("verifying");
    });

    it("reads progress from the live stream, not from the finished payload", () => {
      // The payload does not exist until the run ends. Reading stages out
      // of `run.events` made every intermediate phase unreachable.
      expect(
        phaseOf(
          inputs({
            session,
            runId: "r1",
            run: null,
            events: ev("report_started"),
          }),
        ),
      ).toBe("presenting");
    });

    it("is routing when the stream has told us nothing yet", () => {
      expect(phaseOf(inputs({ session, runId: "r1" }))).toBe("routing");
    });
  });

  describe("terminal states", () => {
    it("reads a refusal as a refusal, not a failure", () => {
      // These are three different things to show a reader, and inferring
      // them from "no findings" conflated all three.
      expect(
        phaseOf(inputs({ session, run: run({ outcome: "refused" }) })),
      ).toBe("refused");
    });

    it("reads a cancelled run as expired", () => {
      expect(
        phaseOf(inputs({ session, run: run({ outcome: "cancelled" }) })),
      ).toBe("expired");
    });

    it("reads any other non-completed outcome as failed", () => {
      for (const outcome of ["failed", "timeout", "budget_exhausted"]) {
        expect(phaseOf(inputs({ session, run: run({ outcome }) }))).toBe(
          "failed",
        );
      }
    });

    it("is completed when the run completed", () => {
      expect(
        phaseOf(inputs({ session, run: run({ outcome: "completed" }) })),
      ).toBe("completed");
    });

    it("treats a payload with no outcome as completed", () => {
      // Payloads predating the field exist, and a recorded run that
      // produced a report is not a failure merely for being old.
      expect(phaseOf(inputs({ session, run: run() }))).toBe("completed");
    });

    it("a finished run outranks a stale busy flag", () => {
      expect(
        phaseOf(
          inputs({
            session,
            runId: "r1",
            run: run({ outcome: "completed" }),
            busy: true,
          }),
        ),
      ).toBe("completed");
    });
  });

  it("never returns a phase outside the declared set", () => {
    const allowed = new Set([
      "booting",
      "choose_dataset",
      "profiling",
      "ready_to_ask",
      "routing",
      "executing",
      "verifying",
      "presenting",
      "completed",
      "refused",
      "failed",
      "expired",
    ]);
    const cases: PhaseInputs[] = [
      inputs({ config: null }),
      inputs({ configError: "x" }),
      inputs(),
      inputs({ busy: true }),
      inputs({ session }),
      inputs({ session, runId: "r" }),
      inputs({ session, run: run({ outcome: "refused" }) }),
      inputs({ session, run: run({ outcome: "timeout" }) }),
      inputs({ session, run: run() }),
    ];
    for (const input of cases) {
      expect(allowed.has(phaseOf(input))).toBe(true);
    }
  });
});

describe("phase predicates", () => {
  it("knows which phases have a report on screen", () => {
    expect(showsReport("completed")).toBe(true);
    expect(showsReport("presenting")).toBe(true);
    for (const phase of [
      "routing",
      "executing",
      "refused",
      "failed",
    ] as const) {
      expect(showsReport(phase)).toBe(false);
    }
  });

  it("knows which phases are idle", () => {
    // The ambient grid may drift only in these two: nothing is running
    // and there is nothing to read.
    expect(isIdle("choose_dataset")).toBe(true);
    expect(isIdle("ready_to_ask")).toBe(true);
    for (const phase of ["routing", "executing", "completed"] as const) {
      expect(isIdle(phase)).toBe(false);
    }
  });
});
