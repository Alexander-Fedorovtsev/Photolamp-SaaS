import { describe, expect, it, vi } from "vitest";
import { CatalogUnavailable, getTenantCatalog } from "./tenant";

const fixture = {
  slug: "demo-ufa",
  display_name: "Демо",
  city: "Уфа",
  timezone: "Asia/Yekaterinburg",
  accent: "sun",
  public_note: "Синтетический каталог",
  is_demo: true,
  booking_enabled: false,
  availability_confirmed: false,
  phone: null,
  branches: [
    {
      id: "d1000000-0000-0000-0000-000000000001",
      name: "Демо-точка",
      city: "Уфа",
      timezone: "Asia/Yekaterinburg",
      pickup_enabled: true,
      delivery_enabled: true,
    },
  ],
  items: [
    {
      id: "d2000000-0000-0000-0000-000000000001",
      name: "Демо-лампа",
      category: "phototherapy_lamp",
      manufacturer: null,
      description: "Синтетическая карточка",
      included_items: ["лампа", "кейс", "инструкция"],
      illustration: "floor",
      amount_minor_per_day: 49000,
      deposit_minor: 0,
      currency: "RUB",
      minimum_days: 3,
      maximum_days: 30,
      price_is_demo: true,
      listed_units: 2,
      serviceable_units: 1,
    },
  ],
};

describe("public tenant catalog boundary", () => {
  it("loads and validates API catalog data", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(fixture));
    expect(await getTenantCatalog("demo-ufa", "http://api:8000", fetcher)).toEqual(fixture);
    expect(fetcher).toHaveBeenCalledWith(
      "http://api:8000/api/v1/r/demo-ufa/catalog",
      expect.objectContaining({ cache: "no-store" }),
    );
  });
  it("does not fall back for an unknown organization", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 }));
    expect(await getTenantCatalog("unknown", "http://api:8000", fetcher)).toBeNull();
  });
  it.each(["../demo-ufa", "DEMO", "x".repeat(64), "", "demo/ufa"])(
    "rejects invalid slug %s",
    async (slug) => {
      const fetcher = vi.fn<typeof fetch>();
      expect(await getTenantCatalog(slug, "http://api:8000", fetcher)).toBeNull();
      expect(fetcher).not.toHaveBeenCalled();
    },
  );
  it("rejects a response for a different tenant", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ ...fixture, slug: "demo-kazan" }));
    await expect(getTenantCatalog("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      CatalogUnavailable,
    );
  });
  it.each([
    { ...fixture, booking_enabled: true },
    { ...fixture, availability_confirmed: true },
    { ...fixture, items: [{ ...fixture.items[0], serviceable_units: 3 }] },
    {},
  ])("rejects invalid or falsely enabled capabilities", async (data) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(data));
    await expect(getTenantCatalog("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      CatalogUnavailable,
    );
  });
  it("reports service failure without fabricating a storefront", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 }));
    await expect(getTenantCatalog("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      CatalogUnavailable,
    );
  });
  it("redacts network errors", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("internal-secret"));
    await expect(getTenantCatalog("demo-ufa", "http://api:8000", fetcher)).rejects.toThrow(
      "Tenant catalog service is unavailable",
    );
  });
});
