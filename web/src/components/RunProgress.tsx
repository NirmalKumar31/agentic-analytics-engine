/**
 * What a run is doing while it runs.
 *
 * Three things used to be here, inside a `<section class="panel">` headed
 * **ANALYSIS**: a static agent DAG, a STAGES card list, and the activity
 * log. The DAG drew the same boxes and arrows for every run and was on
 * screen before anything had happened; the stage cards restated the same
 * five steps a third time, after the stepper and the DAG had each already
 * said them.
 *
 * Then it was the stage sequence alone, derived from backend events. That
 * is enough for a deterministic run, which is over in a second. It is not
 * enough for an AI run: the engine can sit on one stage for half a minute
 * while a provider thinks, and "interpreting with AI", unchanged, for
 * thirty seconds is the same picture as a hung request. A reader waiting
 * on it has no way to tell a slow run from a stuck one, and no idea
 * whether to keep waiting.
 *
 * So three things are added, and each is a **fact** rather than a
 * reassurance:
 *
 *   elapsed        wall clock since this browser saw the run's first
 *                  event. Not computed from event timestamps: those are
 *                  the server's clock, and a skew of a few seconds would
 *                  make the reader's own wait wrong or negative.
 *   work finished  counts of calls returned, findings verified, findings
 *                  withheld, charts drawn, each of which moves only
 *                  when an event says so.
 *   why it waits   on an AI run, that the delay is a provider round trip
 *                  and not the engine stalling.
 *
 * **There is no progress bar, and that is the point.** A percentage needs
 * a denominator, and how many calls a run will make is decided by the
 * planner as it goes. Any bar would be a guess or a timer, and a bar that
 * advances on a timer cannot be told apart from one that advances because
 * work was done, which is the one claim this product cannot afford to
 * get wrong. See `lib/runProgress.ts`.
 */

import { useEffect, useRef, useState } from "react";

import { RunTimeline } from "./RunTimeline";
import { elapsedLabel, progressOf, workDone } from "../lib/runProgress";
import type { RecordingSummary, RunEvent, UiMode } from "../lib/types";

/**
 * Seconds since the first event arrived in this browser.
 *
 * Starts when there is something to time rather than on mount, so the
 * figure is the run's age and not the page's. Stops when the run ends: a
 * counter still climbing under a finished report is measuring nothing.
 */
function useElapsed(started: boolean, finished: boolean): number {
  const [seconds, setSeconds] = useState(0);
  const startedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!started) return;
    startedAt.current ??= Date.now();
    if (finished) return;
    const tick = () => {
      if (startedAt.current === null) return;
      setSeconds((Date.now() - startedAt.current) / 1000);
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [started, finished]);

  return seconds;
}

export function RunProgress({
  events,
  replay,
  mode,
}: {
  events: RunEvent[];
  replay: RecordingSummary | null;
  /** Which planner is running, for the one note that is mode-specific. */
  mode?: UiMode;
}) {
  const progress = progressOf(events);
  const elapsed = useElapsed(events.length > 0, progress.finished);
  const done = workDone(progress);
  const waitsOnProvider = mode === "ai" || mode === "compare";

  return (
    <section className="run-progress" data-testid="run-progress">
      {/*
        One live region for the whole thing, polite.

        The stage, the elapsed figure and the work list all change, and
        announcing each separately narrates three changes for one step
        forward. `RunTimeline` has its own status region for the stage; this
        one carries what finished, which is the part a waiting reader is
        listening for.
      */}
      <p className="sr-only" role="status">
        {progress.stopped
          ? `Run stopped at ${progress.stopped.label}.`
          : progress.finished
            ? "Run complete."
            : done.length > 0
              ? `${progress.active?.label ?? "Running"}. So far: ${done.join(", ")}.`
              : `${progress.active?.label ?? "Running"}. Nothing has finished yet.`}
      </p>

      <div className="run-progress-head">
        <p className="run-progress-stage" data-testid="run-progress-stage">
          {/*
            "Starting" only before there is anything to report. A run with
            events but no active stage is between stages -- every stage the
            engine has reported is complete and the next has not begun --
            and calling that "Starting" told a reader nothing had happened
            while the work list beside it said two queries had returned.
          */}
          {progress.stopped
            ? `Stopped at ${progress.stopped.label}`
            : progress.finished
              ? "Finished"
              : progress.active
                ? `Running: ${progress.active.label}`
                : events.length > 0
                  ? "Running"
                  : "Starting"}
        </p>
        {/* A measurement, so it is tabular: the figure must not jitter
            sideways once a second. */}
        <p className="run-progress-elapsed" data-testid="run-progress-elapsed">
          {elapsedLabel(elapsed)}
        </p>
      </div>

      <RunTimeline events={events} />

      {/*
        What has finished. Absent rather than zeroed: "0 charts drawn" is
        not a result, and a run that has not reached a stage should not
        advertise the stage.
      */}
      {done.length > 0 && (
        <p className="run-progress-work" data-testid="run-progress-work">
          {done.join(" · ")}
        </p>
      )}

      {/*
        Why an AI run is slow, said while it is slow rather than explained
        afterwards. Deliberately not a time estimate: the provider does not
        give one, so neither does this.
      */}
      {waitsOnProvider && !progress.finished && (
        <p className="run-progress-note" data-testid="run-progress-note">
          Waiting on a cloud model. A round trip is usually a few seconds
          and can be longer when the provider is busy; the figures above
          move when the engine reports work, not on a timer.
        </p>
      )}

      {replay && (
        <p className="timeline-provenance">
          recorded run · <span className="mono">{replay.recording_id}</span>
        </p>
      )}
    </section>
  );
}
