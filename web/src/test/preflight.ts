/**
 * The browser suite refuses to run against a paid provider.
 *
 * This exists because of a near miss. A long-lived server owned by another
 * process was listening on 127.0.0.1:8000, which is exactly this suite's
 * default base URL, reporting `provider_mode: cloud`. A uvicorn started
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
 *
 * It lives under `src/test/` rather than beside the specs in `e2e/`, next
 * to the other non-suite helpers there, because the Dockerfile copies
 * `web/src` and not `web/e2e`, and `npm run build` runs `tsc -b`, which
 * type-checks `src/test`. With the module in `e2e/` the image build failed
 * on an unresolvable import while the identical command passed locally,
 * where `e2e/` happens to exist. The build context is part of the
 * contract.
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

/**
 * Is this host the machine the suite is running on?
 *
 * Name or literal: `localhost`, the IPv4 loopback block, and `::1` in the
 * bracketed form a URL hostname is parsed into. `127.0.0.1` is what every
 * sanctioned run uses; the rest are here so an equivalent spelling is not
 * mistaken for a remote host.
 */
function isLoopback(hostname: string): boolean {
  const host = hostname.toLowerCase();
  if (host === "localhost" || host === "::1" || host === "[::1]") return true;
  return /^127(?:\.\d{1,3}){3}$/.test(host);
}

/** The base URL the suite will drive, resolved the one way. */
export function resolveBaseUrl(env: Record<string, string | undefined>): string {
  return env.AAE_E2E_BASE_URL ?? "http://127.0.0.1:8000";
}

/**
 * Refuse unless the target is local and explicitly in fake mode.
 *
 * Every failure mode refuses rather than warning: unreachable, non-200,
 * unparseable, missing field, a remote host, and any provider mode other
 * than `fake`. A check that passed when it could not tell would be worse
 * than none, because it would be relied upon.
 */
export async function assertFakeProvider(
  baseURL: string | undefined,
  probe: Probe,
  env: Record<string, string | undefined> = {},
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

  /*
    The fake-provider check cannot keep this suite off the deployment,
    because the deployment is in fake mode too.

    `provider_mode` selects the *ungoverned* provider. The paid path is
    gated separately, on `ai_analytics_enabled` plus a key plus a
    reachable ledger, which is why `render.yaml` ships
    `AAE_PROVIDER_MODE=fake` and still serves AI Analytics. Production
    therefore answers `/api/health` with exactly the value this gate is
    looking for, and a stray `AAE_E2E_BASE_URL` would send the suite's
    uploads, analyses and session deletions at the live site with every
    check passing.

    So the host is checked as well, and before the probe rather than
    after it: the probe is itself a request, and the point is not to make
    it against somewhere this suite was never meant to touch. Every
    sanctioned run is already loopback -- CI boots a container and points
    at `127.0.0.1:8000`. A deployment is tested by the hosted sweep in
    `web/hosted/`, which has its own gate on `AAE_HOSTED_SHA`.
  */
  if (!isLoopback(parsed.hostname) && env.AAE_E2E_ALLOW_REMOTE !== "1") {
    return refuse(
      baseURL,
      `is not a loopback address (${parsed.hostname}).\n` +
        "A deployment answers /api/health with provider_mode=fake as well, " +
        "so that check alone cannot tell it apart from a local server.\n" +
        "Use the hosted sweep in web/hosted/ to test a deployment, or set " +
        "AAE_E2E_ALLOW_REMOTE=1 if you really mean this host",
    );
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
