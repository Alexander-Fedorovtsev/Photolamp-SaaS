import { describe, expect, it, vi } from "vitest";
import { getTenantPreview, PreviewUnavailable } from "./tenant";

const fixture = {
  slug: "demo-ufa",
  display_name: "Демо",
  city: "Уфа",
  timezone: "Asia/Yekaterinburg",
  accent: "sun",
  is_demo: true,
  booking_enabled: false,
  phone: null,
  tariff: null,
};

describe("public tenant preview boundary", () => {
  it("loads and validates API configuration", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(fixture));
    expect(await getTenantPreview("demo-ufa", "http://api:8000", fetcher)).toEqual(fixture);
    expect(fetcher).toHaveBeenCalledWith(
      "http://api:8000/api/v1/r/demo-ufa/preview",
      expect.objectContaining({ cache: "no-store" }),
    );
  });
  it("does not fall back for an unknown organization", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 }));
    expect(await getTenantPreview("unknown", "http://api:8000", fetcher)).toBeNull();
  });
  it.each(["../demo-ufa", "DEMO", "x".repeat(64), "", "demo/ufa"])(
    "rejects invalid slug %s",
    async (slug) => {
      const fetcher = vi.fn<typeof fetch>();
      expect(await getTenantPreview(slug, "http://api:8000", fetcher)).toBeNull();
      expect(fetcher).not.toHaveBeenCalled();
    },
  );
  it("rejects a response for a different tenant", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ ...fixture, slug: "demo-kazan" }));
    await expect(getTenantPreview("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      PreviewUnavailable,
    );
  });
  it.each([{ ...fixture, booking_enabled: true }, { ...fixture, is_demo: false }, {}])(
    "rejects invalid or falsely enabled capabilities",
    async (data) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(data));
      await expect(getTenantPreview("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
        PreviewUnavailable,
      );
    },
  );
  it("reports service failure without fabricating a storefront", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 }));
    await expect(getTenantPreview("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      PreviewUnavailable,
    );
  });
  it("redacts network errors", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("internal-secret"));
    await expect(getTenantPreview("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      "Tenant preview service is unavailable",
    );
  });
});
