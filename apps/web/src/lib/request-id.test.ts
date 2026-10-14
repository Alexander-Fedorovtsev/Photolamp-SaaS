import { describe, expect, it } from "vitest";
import { requestId, validRequestId } from "./request-id";

describe("request correlation IDs", () => {
  it("preserves constrained caller IDs", () => {
    expect(requestId("monitor-12345678")).toBe("monitor-12345678");
    expect(validRequestId("monitor-12345678")).toBe(true);
  });

  it.each([null, "short", "bad id", "../../secret", "x".repeat(129)])(
    "replaces an unsafe value %s",
    (value) => {
      const generated = requestId(value);
      expect(generated).toMatch(/^[a-f0-9]{32}$/);
      expect(generated).not.toBe(value);
    },
  );
});
