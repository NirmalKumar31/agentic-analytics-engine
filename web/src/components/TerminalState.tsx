/**
 * Problems that are not a run's outcome.
 *
 * Two things only: the server configuration failing to load, and a
 * client-side error with no run behind it -- a rejected upload, an analysis
 * that could not be started. Both happen when there is no run to report on,
 * so nothing else on screen says them.
 *
 * It used to also restate `stopped_reason` as "The run stopped early: ...".
 * That became a third copy of the same sentence once `RunStateCard` started
 * reporting terminal states in single mode: the presentation headline said
 * it, the Notes section said it, and this said it again in a different
 * phrasing. `RunStateCard` says it once, with the state named -- "Refused."
 * rather than "stopped early", so this no longer does.
 */
export function TerminalState({
  configError,
  error,
}: {
  configError: string | null;
  error: string | null;
}) {
  return (
    <>
      {configError && (
        <div className="notice error" role="alert">
          {configError}
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
    </>
  );
}
