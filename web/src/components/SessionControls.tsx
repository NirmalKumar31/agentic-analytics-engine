export function SessionControls({
  hasSession,
  hasRun,
  onEndSession,
  onReset,
}: {
  hasSession: boolean;
  hasRun: boolean;
  onEndSession: () => void;
  onReset: () => void;
}) {
  return (
    <>
      {hasSession && (
        <button
          className="btn ghost small"
          onClick={onEndSession}
          title="Delete this dataset and everything derived from it"
        >
          End session
        </button>
      )}
      {hasRun && (
        <button className="btn ghost small" onClick={onReset}>
          Start over
        </button>
      )}
    </>
  );
}
