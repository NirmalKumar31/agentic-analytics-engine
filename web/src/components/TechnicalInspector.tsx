import { PlanningAudit } from "./PlanningAudit";
import type { RunPayload } from "../lib/types";

export function TechnicalInspector({ run }: { run: RunPayload }) {
  return <PlanningAudit run={run} />;
}
