import type { ReactNode } from "react";

export function AppShell({
  sessionId,
  header,
  workflow,
  hasRun,
  children,
}: {
  sessionId?: string;
  header: ReactNode;
  workflow: ReactNode;
  hasRun: boolean;
  children: ReactNode;
}) {
  return (
    <div className="shell" data-session-id={sessionId}>
      {header}
      {workflow}
      <main className="main" data-rail={hasRun ? "true" : "false"}>
        {children}
      </main>
    </div>
  );
}
