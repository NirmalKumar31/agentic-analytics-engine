/**
 * Refuse to run against anything but the build we were asked to accept.
 *
 * The browser suite's own preflight refuses a target that is not in fake
 * provider mode, because a paid provider was once listening on its default
 * port. This one adds the other half: a hosted acceptance result is
 * evidence about **a specific deployed commit**, and a sweep that ran
 * against whatever happened to be serving proves nothing about the SHA it
 * is filed under. Render redeploys on its own schedule and the tester is
 * not in the loop, so the SHA is checked here rather than assumed.
 *
 * There is no bypass flag, on purpose. A mismatch is a stop, not a warning:
 * the honest outcome of "the deployment is not what you said" is no result
 * at all.
 */

const HEALTH_TIMEOUT_MS = 90_000;

export interface Health {
  build_sha: string;
  provider_mode: string;
  status: string;
  execution_mode?: string;
  recordings?: number;
  demo_warehouse_ready?: boolean;
}

export function requiredSha(env: NodeJS.ProcessEnv): string {
  const sha = (env.AAE_HOSTED_SHA ?? "").trim();
  if (!/^[0-9a-f]{40}$/.test(sha)) {
    throw new Error(
      "AAE_HOSTED_SHA must be the full 40-character commit this deployment is expected to be serving. " +
        `Got ${sha ? `"${sha}"` : "nothing"}.`,
    );
  }
  return sha;
}

export function baseUrl(env: NodeJS.ProcessEnv): string {
  const url = (env.AAE_HOSTED_BASE_URL ?? "").trim();
  if (!url) {
    throw new Error(
      "AAE_HOSTED_BASE_URL must name the deployment to accept, e.g. https://example.onrender.com",
    );
  }
  return url.replace(/\/+$/, "");
}

export async function readHealth(url: string): Promise<Health> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  try {
    const response = await fetch(`${url}/api/health`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`${url}/api/health answered ${response.status}`);
    }
    return (await response.json()) as Health;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The whole gate, as a pure check over a health document.
 *
 * Separated from the fetch so it can be tested without a deployment, and
 * so the reasons are phrased once.
 */
export function refusalFor(health: Health, sha: string): string | null {
  if (health.build_sha !== sha) {
    return (
      `This deployment is serving ${health.build_sha || "an unreported commit"}, ` +
      `not ${sha}. A hosted acceptance result is evidence about one commit; ` +
      "refusing rather than filing a sweep of a different build."
    );
  }
  if (health.provider_mode !== "fake") {
    return (
      `provider_mode is "${health.provider_mode}", not "fake". ` +
      "This sweep makes no provider calls, but it will not run against a " +
      "target where an accidental one would cost money."
    );
  }
  if (health.status !== "ok") {
    return `The deployment reports status "${health.status}".`;
  }
  if ((health.recordings ?? 0) < 1) {
    return (
      "The deployment advertises no recordings. The successful-report cases " +
      "replay a recorded run rather than starting one, so there is nothing " +
      "to show and nothing that may be substituted for it."
    );
  }
  return null;
}

export default async function globalSetup(): Promise<void> {
  const url = baseUrl(process.env);
  const sha = requiredSha(process.env);
  const health = await readHealth(url);
  const refusal = refusalFor(health, sha);
  if (refusal) throw new Error(`Hosted acceptance refused. ${refusal}`);

  console.log(
    [
      `Hosted acceptance target: ${url}`,
      `  build_sha      ${health.build_sha}`,
      `  provider_mode  ${health.provider_mode}`,
      `  execution      ${health.execution_mode ?? "not reported"}`,
      `  recordings     ${health.recordings ?? 0}`,
      "  spend          none: no upload, no analysis, no comparison, no provider call",
    ].join("\n"),
  );
}
