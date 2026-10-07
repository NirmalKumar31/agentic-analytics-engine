import { badgeMode, ModeBadge } from "./ModeBadge";
import { SessionControls } from "./SessionControls";
import { ThemeToggle, type Theme } from "./ThemeToggle";
import type { ServerConfig, UiMode } from "../lib/types";

export function ProductHeader({
  config,
  hasRun,
  hasSession,
  replaying,
  uiMode,
  theme,
  onToggleTheme,
  onEndSession,
  onReset,
}: {
  config: ServerConfig | null;
  hasRun: boolean;
  hasSession: boolean;
  replaying: boolean;
  uiMode: UiMode;
  theme: Theme;
  onToggleTheme: () => void;
  onEndSession: () => void;
  onReset: () => void;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <BrandMark />
        <span className="brand-name">Agentic Analytics</span>
      </div>
      {/* Dataset identity lives in `DatasetContextBar`, immediately below
          this header, and only there. It was briefly in both places and the
          screen then read "Your file · Uploaded file: sales.csv · 240 rows"
          above "Uploaded file: sales.csv · 240 rows · 5 fields" -- the same
          fact twice, in two type treatments, which is how a reader learns to
          stop reading chrome. */}
      {/* The tagline that used to sit here -- "bounded analysis · MCP tools ·
          provenance on every number" -- is gone. It described the product to
          someone deciding whether to use it, and it was on screen for every
          second afterwards, in the chrome, above every answer. An instrument's
          header says what you are looking at, not what the instrument is for.
          The claims it made are all demonstrated by the report itself. */}
      <div className="topbar-spacer" />
      {config && (hasRun || replaying) && (
        <ModeBadge
          mode={badgeMode(replaying, config.execution_mode, uiMode)}
        />
      )}
      <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      <SessionControls
        hasSession={hasSession}
        hasRun={hasRun}
        onEndSession={onEndSession}
        onReset={onReset}
      />
    </header>
  );
}

/**
 * The product mark: a measured field.
 *
 * A baseline, two risers of differing height, and one plotted reading. It is
 * the same contour geometry as the rest of the visual signature, and it is
 * the smallest honest picture of what this product does. It measures
 * something and plots where the measurement landed.
 *
 * What it replaces was a rising line inside a rounded square: a chart going
 * up, which is a claim about results rather than a description of an
 * instrument. This engine publishes margin falling as readily as revenue
 * rising, and the mark should not promise one of them.
 *
 * It is not a logotype, it carries no text, and it never appears inside the
 * report canvas, once, in the header, is the whole budget.
 */
function BrandMark() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 18 18"
      fill="none"
      aria-hidden="true"
    >
      <g
        stroke="var(--signal)"
        strokeWidth="1.8"
        strokeLinecap="round"
      >
        <path d="M1 15 L17 15" />
        <path d="M4 15 L4 9" />
        <path d="M9 15 L9 5" />
      </g>
      <circle cx="14" cy="7" r="2.2" fill="var(--signal)" />
    </svg>
  );
}
