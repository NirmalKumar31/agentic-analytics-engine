import { RightRail } from "./RightRail";
import type { DatasetCatalog, MetricInfo } from "../lib/types";

export function DatasetIdentity({
  catalog,
  metrics,
}: {
  catalog: DatasetCatalog | null;
  metrics: MetricInfo[];
}) {
  return (
    <RightRail
      catalog={catalog}
      metrics={metrics}
      usedMetrics={[]}
      results={{}}
      tasks={[]}
      runMetrics={null}
      onOpenResult={() => undefined}
    />
  );
}
