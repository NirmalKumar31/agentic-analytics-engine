/**
 * Every highlight a run published is on screen, at every width.
 *
 * This replaces `findingFold.test.tsx`, which asserted a phone-width fold
 * that product output could not reach.
 *
 * The fold showed one highlight below 640px and hid the rest behind a
 * `<details>`, above a threshold of three. **The presentation contract
 * emits at most two highlights for any shape it builds**: highest and
 * lowest, so the threshold was never met. It looked alive only because
 * the published recordings carried no presentation snapshot and the report
 * fell back to listing the engine's own findings, which is the defect
 * `presentation/fields.py` and `recordings/record.py` were corrected for.
 *
 * Keeping the branch for a constructed model would have left dead markup,
 * a dead constant, a dead media query and a disclosure a keyboard user
 * could reach and nobody could explain. So the branch is gone and this is
 * what is asserted in its place, over payloads the pipeline produced.
 *
 * The cap itself is asserted here rather than described in a comment. If
 * the contract starts emitting more highlights, this fails and says so,
 * which is the signal that a fold is worth building again, in that order:
 * raise the cap, produce a recording that reaches it, then build the fold.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReportWorkspace } from "../components/ReportWorkspace";
import type { RunPayload } from "../lib/types";

function load(where: string, name: string): RunPayload {
  return JSON.parse(readFileSync(join(__dirname, "..", "..", "..", where, name), "utf8"));
}

/** Everything a credential-free visitor can open, plus the upload shapes. */
const RECORDINGS = ["margin-q3", "returns-segments", "shipping-repeat"].map((name) => [
  name,
  load("examples/recordings", `${name}.json`),
]) as [string, RunPayload][];

const UPLOADS = ["trend", "grouped", "ranking", "scalar", "high-cardinality"].map((name) => [
  name,
  JSON.parse(readFileSync(join(__dirname, "runs", `${name}.json`), "utf8")),
]) as [string, RunPayload][];

function workspace(run: RunPayload) {
  return render(
    <ReportWorkspace
      comparison={null}
      run={run}
      aiRun={null}
      aiError={null}
      config={null}
      deterministicPending={false}
    />,
  );
}

const highlightCount = () => document.querySelectorAll(".finding-item").length;

/*
 * Two groups, because two rendering paths reach this component.
 *
 * `fromPresentation` lists `presentation.highlights`, which is what every
 * completed run produces. `fromRun` lists the engine's own findings and is
 * reached only when no presentation could be built, such as a refusal or a
 * failure, which publish at most one. Some committed payloads here predate
 * the presentation snapshot and still exercise that path, which is why
 * they are kept and why the counts are asserted against the right source.
 *
 * Neither path folds anything, and that is asserted of both.
 */
const WITH_PRESENTATION = [...RECORDINGS, ...UPLOADS].filter(
  ([, run]) => run.presentation != null,
);
const FALLBACK = [...RECORDINGS, ...UPLOADS].filter(([, run]) => run.presentation == null);

describe("no published report folds anything", () => {
  for (const [name, run] of [...WITH_PRESENTATION, ...FALLBACK]) {
    describe(name, () => {
      it("renders no fold control", () => {
        workspace(run);
        expect(document.querySelector(".findings-more")).toBeNull();
        expect(
          screen.queryByText(/\d+ more findings?/),
          "a disclosure offering to reveal findings is still in the markup",
        ).toBeNull();
      });

      it("leaves every item directly in the list", () => {
        workspace(run);
        const direct = document.querySelectorAll(
          ".findings > .finding-list > .finding-item",
        ).length;
        expect(direct).toBe(highlightCount());
      });

      it("hides none of them behind a disclosure", () => {
        workspace(run);
        expect(document.querySelectorAll("details .finding-item").length).toBe(0);
      });
    });
  }
});

describe("a presentation report shows every highlight it published", () => {
  for (const [name, run] of WITH_PRESENTATION) {
    it(name, () => {
      workspace(run);
      expect(highlightCount()).toBe(run.presentation!.highlights.length);
    });
  }

  it("covers the three recordings a credential-free visitor can open", () => {
    const names = WITH_PRESENTATION.map(([name]) => name);
    for (const recording of ["margin-q3", "returns-segments", "shipping-repeat"]) {
      expect(names).toContain(recording);
    }
  });
});

describe("the presentation contract's highlight cap", () => {
  /*
   * Asserted, not documented. The fold's threshold was three and the cap
   * is two; the gap between those numbers is the whole reason the branch
   * was unreachable, and a comment would not have noticed the cap moving.
   */
  const CAP = 2;

  for (const [name, run] of WITH_PRESENTATION) {
    it(`${name} publishes at most ${CAP} highlights`, () => {
      const published = run.presentation!.highlights.length;
      expect(
        published,
        `${name} publishes ${published} highlights. If the contract now emits more ` +
          "than three, a reader-facing fold is worth building again -- raise the cap, " +
          "produce a recording through the pipeline that reaches it, then build the fold.",
      ).toBeLessThanOrEqual(CAP);
    });
  }

  it("is below the threshold a fold would need, which is why there is none", () => {
    expect(CAP).toBeLessThan(3);
  });
});
