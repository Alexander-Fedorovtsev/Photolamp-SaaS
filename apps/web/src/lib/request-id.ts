export const requestIdHeader = "X-Request-ID";

const requestIdPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$/;

export function validRequestId(value: string | null | undefined) {
  return typeof value === "string" && requestIdPattern.test(value);
}

export function requestId(value?: string | null) {
  if (value && validRequestId(value)) return value;
  return crypto.randomUUID().replaceAll("-", "");
}
