import { describe, expect, it } from "vitest";
import { adminCatalogSchema, adminOrderPageSchema, membershipSchema } from "./admin";

const staffOrderFixture = {
  order_id: "d9000000-0000-0000-0000-000000000001",
  state: "held",
  source: "website",
  snapshot: {
    schema_version: 1,
    model_id: "d2000000-0000-0000-0000-000000000001",
    model_name: "Synthetic lamp",
    branch_id: "d1000000-0000-0000-0000-000000000001",
    branch_name: "Synthetic branch",
    start_date: "2030-01-10",
    end_date: "2030-01-12",
    fulfillment: "pickup",
    quote: {
      model_id: "d2000000-0000-0000-0000-000000000001",
      branch_id: "d1000000-0000-0000-0000-000000000001",
      start_date: "2030-01-10",
      end_date: "2030-01-12",
      days: 3,
      available_units: 1,
      available: true,
      availability_confirmed: true,
      reservation_created: false,
      fulfillment: "pickup",
      amount_minor_per_day: 49_000,
      rental_amount_minor: 147_000,
      deposit_minor: 0,
      delivery_amount_minor: 0,
      total_minor: 147_000,
      currency: "RUB",
      price_is_demo: true,
      booking_enabled: false,
    },
  },
  identity_verified: false,
  payment_status: "not_started",
  expires_at: "2030-01-01T12:15:00+00:00",
  confirmed_at: null,
  confirmed_by_staff_user_id: null,
  created_at: "2030-01-01T12:00:00+00:00",
  updated_at: "2030-01-01T12:00:00+00:00",
} as const;

describe("tenant admin contracts", () => {
  it("validates a tenant-scoped catalog snapshot", () => {
    const snapshot = adminCatalogSchema.parse({
      tenant_id: "d0000000-0000-0000-0000-000000000001",
      role: "owner",
      branches: [
        {
          id: "d1000000-0000-0000-0000-000000000001",
          name: "Synthetic branch",
          city: "Synthetic city",
          timezone: "Europe/Moscow",
          pickup_enabled: true,
          delivery_enabled: false,
          state: "active",
        },
      ],
      models: [],
      assets: [],
      pricing: [],
    });
    expect(snapshot.role).toBe("owner");
    expect(snapshot.branches).toHaveLength(1);
  });

  it("rejects unknown staff roles and malformed asset states", () => {
    expect(
      membershipSchema.safeParse({
        tenant_id: "tenant",
        slug: "demo",
        display_name: "Demo",
        timezone: "UTC",
        role: "superadmin",
      }).success,
    ).toBe(false);
    expect(
      adminCatalogSchema.safeParse({
        tenant_id: "tenant",
        role: "owner",
        branches: [],
        models: [],
        assets: [
          {
            id: "asset",
            branch_id: "branch",
            branch_name: "Branch",
            equipment_model_id: "model",
            model_name: "Model",
            asset_code: "DEMO-01",
            serial_no: null,
            state: "lost",
          },
        ],
        pricing: [],
      }).success,
    ).toBe(false);
  });

  it("validates the privacy-minimized staff order queue", () => {
    const page = adminOrderPageSchema.parse({
      items: [staffOrderFixture],
      total: 1,
      limit: 100,
      offset: 0,
    });
    expect(page.items[0]?.snapshot.quote.total_minor).toBe(147_000);
  });

  it("rejects accidental customer contact fields in an order payload", () => {
    const parsed = adminOrderPageSchema.safeParse({
      items: [{ ...staffOrderFixture, phone: "+70000000000" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    expect(parsed.success).toBe(false);
  });
});
