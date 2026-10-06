import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LandingView } from "../components/LandingView";
import type { ServerConfig } from "../lib/types";

const config = {
  version: "test",
  build_sha: "test",
  provider_mode: "fake",
  execution_mode: "ai_live",
  uploads_enabled: true,
  max_upload_mb: 10,
  max_upload_columns: 200,
  session_ttl_minutes: 15,
  model_inference_remote: true,
  live_analytics_enabled: true,
  demo_warehouse_ready: false,
  budgets: {},
  demo_questions: [],
  recordings: [],
  capabilities: { modes: [], compare_available: true, ai_limits: null },
} satisfies ServerConfig;

describe("landing data disclosure", () => {
  it("does not imply that default Governed Analysis is always model-free", () => {
    render(
      <LandingView
        config={config}
        session={null}
        replay={null}
        busy={false}
        onDemo={vi.fn()}
        onUploadClick={vi.fn()}
        onFile={vi.fn()}
        onRecording={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/Governed Analysis may use a model when local rules cannot resolve/),
    ).toBeVisible();
    expect(
      screen.queryByText(/nothing is sent to a model unless you choose an AI strategy/i),
    ).toBeNull();
    const disclosure = screen
      .getByText("What can be sent to OpenAI", { selector: "summary" })
      .closest("details");
    expect(disclosure).not.toBeNull();
    expect(disclosure).toHaveTextContent(
      /When a run uses AI, your question, column names/,
    );
  });
});
