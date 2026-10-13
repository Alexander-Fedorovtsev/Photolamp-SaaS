import { describe, expect, it } from "vitest";
import { adminCatalogSchema, membershipSchema } from "./admin";

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
});
