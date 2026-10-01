import { DatasetSummary } from "./DatasetSummary";
import type { DatasetSummary as DatasetSummaryPayload } from "../lib/types";

export function SchemaInspector({
  summary,
  onAsk,
}: {
  summary: DatasetSummaryPayload;
  onAsk: (question: string) => void;
}) {
  return <DatasetSummary summary={summary} onAsk={onAsk} />;
}
