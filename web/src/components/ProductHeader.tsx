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
        <div>
          Agentic Analytics Engine
          <br />
          <small>bounded analysis · MCP tools · provenance on every number</small>
        </div>
      </div>
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

function BrandMark() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="1"
        y="1"
        width="22"
        height="22"
        rx="5"
        stroke="var(--border-strong)"
      />
      <path
        d="M5 16.5 L9.5 10 L13.5 13.5 L19 6.5"
        stroke="var(--accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="9.5" cy="10" r="1.6" fill="var(--supported)" />
      <circle cx="19" cy="6.5" r="1.6" fill="var(--supported)" />
    </svg>
  );
}
