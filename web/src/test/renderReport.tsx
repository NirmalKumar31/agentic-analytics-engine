/**
 * The report under test, with the props the old components took.
 *
 * `ReportView` and `PresentationReportView` are gone: one `AnswerReport`
 * now serves both payload shapes, driven by `reportModel`. This shim takes
 * the inputs those two components took so the suites written against them
 * keep asserting the same claims about the same data, which is the point
 * of porting them rather than deleting them.
 *
 * It lives in `src/test` and is never imported by the application.
 */

import { AnswerReport } from "../components/AnswerReport";
import { reportModel } from "../lib/reportModel";
import type {
  AnalysisPresentation,
  ChartSpec,
  Finding,
  QueryContract,
  Report,
  ResultSnapshot,
  Verdict,
} from "../lib/types";

export function ReportUnderTest({
  question,
  report = null,
  findings = [],
  rejected = [],
  charts = [],
  results = {},
  queryContract = null,
  presentation = null,
  onShowEvidence,
}: {
  question: string;
  report?: Report | null;
  findings?: Finding[];
  rejected?: Verdict[];
  charts?: ChartSpec[];
  results?: Record<string, ResultSnapshot>;
  queryContract?: QueryContract | null;
  presentation?: AnalysisPresentation | null;
  onShowEvidence?: () => void;
  /** Accepted and ignored: the per-finding drawer it opened is gone. */
  onShowWork?: (findingId: string) => void;
}) {
  return (
    <AnswerReport
      question={question}
      model={reportModel({
        presentation,
        report,
        findings,
        rejected,
        charts,
        results,
        queryContract,
      })}
      publishedCount={findings.length}
      withheldCount={rejected.length}
      onShowEvidence={onShowEvidence ?? (() => undefined)}
    />
  );
}
