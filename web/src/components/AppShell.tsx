import type { ReactNode } from "react";

export function AppShell({
  sessionId,
  header,
  hasRun,
  children,
}: {
  sessionId?: string;
  header: ReactNode;
  hasRun: boolean;
  children: ReactNode;
}) {
  return (
    // `data-testid` is the end-to-end suite's readiness marker: it exists
    // only once React has mounted the shell, which is what every test
    // actually needs before it begins. See `openApp` in e2e/helpers.ts.
    <div className="shell" data-testid="app-shell" data-session-id={sessionId}>
      {header}
      <main className="main" data-rail={hasRun ? "true" : "false"}>
        {children}
      </main>
    </div>
  );
}
