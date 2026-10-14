import type { NextRequest } from "next/server";

const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function firstHeaderValue(value: string | null) {
  return value?.split(",", 1)[0]?.trim() ?? "";
}

function effectiveOrigin(request: NextRequest) {
  const host =
    firstHeaderValue(request.headers.get("x-forwarded-host")) || request.headers.get("host");
  const forwardedProtocol = firstHeaderValue(request.headers.get("x-forwarded-proto"));
  const protocol =
    forwardedProtocol === "http" || forwardedProtocol === "https"
      ? forwardedProtocol
      : request.nextUrl.protocol.slice(0, -1);
  if (!host || (protocol !== "http" && protocol !== "https")) return null;
  try {
    return new URL(`${protocol}://${host}`).origin;
  } catch {
    return null;
  }
}

export function isSameOriginRequest(request: NextRequest) {
  if (!unsafeMethods.has(request.method.toUpperCase())) return true;
  const suppliedOrigin = request.headers.get("origin");
  const expectedOrigin = effectiveOrigin(request);
  if (!suppliedOrigin || !expectedOrigin || suppliedOrigin === "null") return false;
  try {
    const parsed = new URL(suppliedOrigin);
    return suppliedOrigin === parsed.origin && parsed.origin === expectedOrigin;
  } catch {
    return false;
  }
}

export function secureSessionCookieRequired(request: NextRequest) {
  const environment = process.env.APP_ENV;
  if (environment === "staging" || environment === "production") return true;
  const forwardedProtocol = firstHeaderValue(request.headers.get("x-forwarded-proto"));
  if (forwardedProtocol === "https" || request.nextUrl.protocol === "https:") return true;
  return (
    environment !== "development" && environment !== "test" && process.env.NODE_ENV === "production"
  );
}
