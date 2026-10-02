/**
 * The browser suite refuses to run against a paid provider.
 *
 * This exists because of a near miss. A long-lived server owned by another
 * process was listening on 127.0.0.1:8000 -- which is exactly this suite's
 * default base URL -- reporting `provider_mode: cloud`. A uvicorn started
 * with `AAE_PROVIDER_MODE=fake` failed to bind that port, exited, and the
 * health response read back belonged to the other server. The posture you
 * set applies to the server you started, which was not the one answering.
 *
 * So the check is not advice in a runbook, it is a gate in the harness: it
 * runs before the first page, upload or analysis request, and it aborts the
 * whole suite rather than one test.
 *
 * There is deliberately no environment-variable bypass. A bypass is the
 * thing that gets set once while debugging and left set. Paid acceptance
 * belongs to its own purpose-built scripts; the ordinary Playwright suite
 * is credential-free by construction.
 */

/** Just enough of a response for the check to be driven by a test. */
export interface HealthProbe {
  status: number;
  body: string;
}

export type Probe = (url: string) => Promise<HealthProbe>;

/** Thrown to abort the suite. Named so the reason survives in the log. */
export class PreflightRefusal extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PreflightRefusal";
  }
}

function refuse(baseURL: string, reason: string): never {
  throw new PreflightRefusal(
    `E2E safety check refused to run:\n${baseURL} ${reason}.\n` +
      `Browser tests require an explicitly fake provider.`,
  );
}

/** The base URL the suite will drive, resolved the one way. */
export function resolveBaseUrl(env: Record<string, string | undefined>): string {
  return env.AAE_E2E_BASE_URL ?? "http://127.0.0.1:8000";
}

/**
 * Refuse unless the target is reachable and explicitly in fake mode.
 *
 * Every failure mode refuses rather than warning: unreachable, non-200,
 * unparseable, missing field, and any provider mode other than `fake`.
 * A check that passed when it could not tell would be worse than none,
 * because it would be relied upon.
 */
export async function assertFakeProvider(
  baseURL: string | undefined,
  probe: Probe,
): Promise<void> {
  if (!baseURL || !baseURL.trim()) {
    throw new PreflightRefusal(
      "E2E safety check refused to run:\nno base URL was configured.\n" +
        "Browser tests require an explicitly fake provider.",
    );
  }

  let parsed: URL;
  try {
    parsed = new URL(baseURL);
  } catch {
    return refuse(baseURL, "is not a valid URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return refuse(baseURL, `is not an http(s) URL (${parsed.protocol})`);
  }

  const target = new URL("/api/health", parsed).toString();

  let response: HealthProbe;
  try {
    response = await probe(target);
  } catch (reason) {
    const detail = reason instanceof Error ? reason.message : String(reason);
    return refuse(baseURL, `is unreachable (${detail})`);
  }

  if (response.status !== 200) {
    return refuse(baseURL, `answered /api/health with HTTP ${response.status}`);
  }

  let health: unknown;
  try {
    health = JSON.parse(response.body);
  } catch {
    return refuse(baseURL, "answered /api/health with something that is not JSON");
  }
  if (typeof health !== "object" || health === null) {
    return refuse(baseURL, "answered /api/health with something that is not an object");
  }

  const mode = (health as Record<string, unknown>).provider_mode;
  if (typeof mode !== "string" || !mode) {
    return refuse(baseURL, "reports no provider_mode");
  }
  if (mode !== "fake") {
    return refuse(baseURL, `reports provider_mode=${mode}`);
  }
}
