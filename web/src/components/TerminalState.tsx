export function TerminalState({
  configError,
  error,
  stoppedReason,
}: {
  configError: string | null;
  error: string | null;
  stoppedReason?: string | null;
}) {
  return (
    <>
      {configError && <div className="notice error">{configError}</div>}
      {error && <div className="notice error">{error}</div>}
      {stoppedReason && (
        <div className="notice warn">The run stopped early: {stoppedReason}</div>
      )}
    </>
  );
}
