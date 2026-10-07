/**
 * The gate that keeps the browser suite credential-free.
 *
 * A paid-provider server was once listening on this suite's default port,
 * and `/api/health` was the only thing that distinguished it from the
 * intended target. These tests pin that the harness refuses, rather than
 * warns, in every case where it cannot prove the provider is fake --
 * including the cases where it cannot tell at all.
 */

import { describe, expect, it, vi } from "vitest";

import {
  assertFakeProvider,
  PreflightRefusal,
  resolveBaseUrl,
  type HealthProbe,
} from "./preflight";

const ok = (body: unknown): HealthProbe => ({ status: 200, body: JSON.stringify(body) });

describe("the e2e provider preflight", () => {
  it("passes against an explicitly fake provider", async () => {
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
    await expect(
      assertFakeProvider("http://127.0.0.1:8111", probe),
    ).resolves.toBeUndefined();
    expect(probe).toHaveBeenCalledWith("http://127.0.0.1:8111/api/health");
  });

  it("refuses a cloud provider, naming it", async () => {
    // The near miss this exists for: the mode read back belonged to a
    // server this process never started.
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "cloud" }));
    await expect(
      assertFakeProvider("http://127.0.0.1:8000", probe),
    ).rejects.toThrow(PreflightRefusal);
    await expect(
      assertFakeProvider("http://127.0.0.1:8000", probe),
    ).rejects.toThrow(/reports provider_mode=cloud/);
    await expect(
      assertFakeProvider("http://127.0.0.1:8000", probe),
    ).rejects.toThrow(/require an explicitly fake provider/);
  });

  it("refuses any mode that is not exactly fake", async () => {
    // Not an allow-list of known-paid names: anything but `fake` refuses,
    // so a provider mode added later is refused by default rather than
    // silently permitted.
    for (const mode of ["cloud", "openai", "anthropic", "local", "Fake", "FAKE", ""]) {
      const probe = vi.fn().mockResolvedValue(ok({ provider_mode: mode }));
      await expect(assertFakeProvider("http://127.0.0.1:8000", probe)).rejects.toThrow(
        PreflightRefusal,
      );
    }
  });

  it("refuses when health is unreachable", async () => {
    const probe = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    await expect(assertFakeProvider("http://127.0.0.1:9999", probe)).rejects.toThrow(
      /is unreachable \(ECONNREFUSED\)/,
    );
  });

  it("refuses a non-200 health response", async () => {
    const probe = vi.fn().mockResolvedValue({ status: 503, body: "" });
    await expect(assertFakeProvider("http://127.0.0.1:8000", probe)).rejects.toThrow(
      /answered \/api\/health with HTTP 503/,
    );
  });

  it("refuses a body that is not JSON", async () => {
    // A proxy or a login page answering 200 with HTML must not read as a
    // pass just because the request succeeded.
    const probe = vi.fn().mockResolvedValue({ status: 200, body: "<html>hi</html>" });
    await expect(assertFakeProvider("http://127.0.0.1:8000", probe)).rejects.toThrow(
      /not JSON/,
    );
  });

  it("refuses JSON that is not an object", async () => {
    const probe = vi.fn().mockResolvedValue({ status: 200, body: '"ok"' });
    await expect(assertFakeProvider("http://127.0.0.1:8000", probe)).rejects.toThrow(
      /not an object/,
    );
  });

  it("refuses a health payload with no provider_mode at all", async () => {
    const probe = vi.fn().mockResolvedValue(ok({ status: "ok" }));
    await expect(assertFakeProvider("http://127.0.0.1:8000", probe)).rejects.toThrow(
      /reports no provider_mode/,
    );
  });

  it("refuses a missing or blank base URL", async () => {
    const probe = vi.fn();
    for (const value of [undefined, "", "   "]) {
      await expect(assertFakeProvider(value, probe)).rejects.toThrow(
        /no base URL was configured/,
      );
    }
    expect(probe).not.toHaveBeenCalled();
  });

  it("refuses a malformed or non-http base URL", async () => {
    const probe = vi.fn();
    await expect(assertFakeProvider("not a url", probe)).rejects.toThrow(
      /is not a valid URL/,
    );
    await expect(assertFakeProvider("file:///etc/hosts", probe)).rejects.toThrow(
      /is not an http\(s\) URL/,
    );
    // Nothing was contacted: the refusal happens before any request.
    expect(probe).not.toHaveBeenCalled();
  });

  it("asks only for health, before anything else could be requested", async () => {
    // The gate's value is its ordering. If it ever probed something after
    // an upload, the upload would already have happened.
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
    await assertFakeProvider("http://127.0.0.1:8111/some/base", probe);
    expect(probe).toHaveBeenCalledTimes(1);
    const [url] = probe.mock.calls[0] as [string];
    expect(url).toBe("http://127.0.0.1:8111/api/health");
    expect(url).not.toMatch(/upload|analys/);
  });

  it("defaults to the local port and honours an override", () => {
    expect(resolveBaseUrl({})).toBe("http://127.0.0.1:8000");
    expect(resolveBaseUrl({ AAE_E2E_BASE_URL: "http://127.0.0.1:8111" })).toBe(
      "http://127.0.0.1:8111",
    );
  });

  it("refuses a remote host even when it reports fake", async () => {
    // The case the provider check cannot catch. `render.yaml` ships
    // `AAE_PROVIDER_MODE=fake` and the deployment still serves AI
    // Analytics, so production answers with exactly the value this gate
    // wants. Without the host check the suite would upload, analyse and
    // delete sessions against the live site, passing every assertion.
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
    await expect(
      assertFakeProvider("https://agentic-analytics-engine.onrender.com", probe),
    ).rejects.toThrow(PreflightRefusal);
  });

  it("names the hosted sweep rather than only refusing", async () => {
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
    await expect(
      assertFakeProvider("https://example.com", probe),
    ).rejects.toThrow(/hosted sweep|AAE_E2E_ALLOW_REMOTE/);
  });

  it("refuses a remote host before it sends the probe", async () => {
    // The probe is itself a request. Refusing after it would still have
    // touched the host this gate exists to stay away from.
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
    await expect(assertFakeProvider("https://example.com", probe)).rejects.toThrow(
      PreflightRefusal,
    );
    expect(probe).not.toHaveBeenCalled();
  });

  it("accepts every spelling of the local machine", async () => {
    for (const url of [
      "http://127.0.0.1:8000",
      "http://127.0.0.2:8000",
      "http://localhost:8000",
      "http://LOCALHOST:8000",
      "http://[::1]:8000",
    ]) {
      const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
      await expect(assertFakeProvider(url, probe)).resolves.toBeUndefined();
    }
  });

  it("allows a remote host only when it is asked for explicitly", async () => {
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "fake" }));
    await expect(
      assertFakeProvider("https://example.com", probe, { AAE_E2E_ALLOW_REMOTE: "1" }),
    ).resolves.toBeUndefined();
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("the remote opt-in does not also excuse a cloud provider", async () => {
    // Two independent gates. Asking for a remote host must not buy a
    // waiver on the one that keeps the suite off a paid provider.
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "cloud" }));
    await expect(
      assertFakeProvider("https://example.com", probe, { AAE_E2E_ALLOW_REMOTE: "1" }),
    ).rejects.toThrow(/provider_mode=cloud/);
  });

  it("has no bypass: nothing in the environment makes a cloud provider acceptable", async () => {
    // A bypass is the flag that gets set once while debugging and left
    // set, so these are named and refused rather than merely absent.
    //
    // `assertFakeProvider` does read one environment value, the remote
    // host opt-in, so this can no longer claim the function ignores the
    // environment altogether. What it claims instead is narrower and is
    // the part that matters: no value, including that opt-in, makes a
    // provider mode other than `fake` acceptable.
    const probe = vi.fn().mockResolvedValue(ok({ provider_mode: "cloud" }));
    for (const key of [
      "AAE_E2E_ALLOW_CLOUD",
      "AAE_E2E_SKIP_PREFLIGHT",
      "CI",
      "AAE_PROVIDER_MODE",
      "AAE_E2E_ALLOW_REMOTE",
    ]) {
      await expect(
        assertFakeProvider("http://127.0.0.1:8000", probe, { [key]: "1" }),
      ).rejects.toThrow(PreflightRefusal);
    }
  });
});
