import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { isSameOriginRequest, secureSessionCookieRequired } from "./request-security";

function request(url: string, method: string, headers: Record<string, string> = {}) {
  return new NextRequest(url, { method, headers });
}

describe("server request security", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("allows safe methods without an Origin header", () => {
    expect(isSameOriginRequest(request("https://photolamp.example/api/admin/session", "GET"))).toBe(
      true,
    );
  });

  it("accepts only the effective origin for state-changing requests", () => {
    expect(
      isSameOriginRequest(
        request("http://web:3000/api/admin/session", "POST", {
          origin: "https://photolamp.example",
          host: "web:3000",
          "x-forwarded-host": "photolamp.example",
          "x-forwarded-proto": "https",
        }),
      ),
    ).toBe(true);
    expect(
      isSameOriginRequest(
        request("https://photolamp.example/api/admin/session", "DELETE", {
          origin: "https://attacker.example",
        }),
      ),
    ).toBe(false);
    expect(
      isSameOriginRequest(request("https://photolamp.example/api/admin/session", "PATCH")),
    ).toBe(false);
  });

  it("requires Secure cookies in staging and production", () => {
    vi.stubEnv("APP_ENV", "staging");
    expect(secureSessionCookieRequired(request("http://localhost/api/admin/session", "POST"))).toBe(
      true,
    );
    vi.stubEnv("APP_ENV", "production");
    expect(secureSessionCookieRequired(request("http://localhost/api/admin/session", "POST"))).toBe(
      true,
    );
    vi.stubEnv("APP_ENV", "development");
    expect(secureSessionCookieRequired(request("http://localhost/api/admin/session", "POST"))).toBe(
      false,
    );
    expect(
      secureSessionCookieRequired(
        request("http://localhost/api/admin/session", "POST", {
          "x-forwarded-proto": "https",
        }),
      ),
    ).toBe(true);
  });
});
