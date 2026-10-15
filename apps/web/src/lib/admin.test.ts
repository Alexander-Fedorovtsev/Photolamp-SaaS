import { describe, expect, it } from "vitest";
import {
  adminCatalogSchema,
  adminOrderContactAccessSchema,
  adminOrderPageSchema,
  membershipSchema,
  staffInvitationCreatedSchema,
  staffInvitationSummarySchema,
  staffSessionRevokedSchema,
  staffSessionSummarySchema,
  operationsDashboardSchema,
  teamSnapshotSchema,
} from "./admin";

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
  planned_end_date: "2030-01-12",
  is_overdue: false,
  overdue_days: 0,
  extension_request: null,
  return_request: null,
  identity_verified: false,
  payment_status: "not_started",
  expires_at: "2030-01-01T12:15:00+00:00",
  confirmed_at: null,
  handed_over_at: null,
  returned_at: null,
  completed_at: null,
  inspection_outcome: null,
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

  it("validates contact data only through its dedicated response", () => {
    const access = adminOrderContactAccessSchema.parse({
      contact: {
        phone_e164: "+70000000000",
        recipient_name: "Synthetic customer",
        delivery_address: null,
      },
      replayed: false,
    });
    expect(access.contact.phone_e164).toBe("+70000000000");
  });

  it("validates a privacy-minimized operations dashboard", () => {
    const dashboard = operationsDashboardSchema.parse({
      tenant_id: "d0000000-0000-0000-0000-000000000001",
      timezone: "Asia/Yekaterinburg",
      local_date: "2030-01-10",
      generated_at: "2030-01-10T06:00:00+00:00",
      counts: {
        awaiting_confirmation: 1,
        handovers_due: 0,
        returns_today: 0,
        returns_tomorrow: 0,
        overdue: 0,
        inspection: 0,
        pending_requests: 0,
      },
      inventory: { available: 2, rented: 0, inspection: 0, maintenance: 0, retired: 0 },
      handovers_due: [],
      returns_today: [],
      returns_tomorrow: [],
      overdue: [],
      pending_requests: [],
    });
    expect(dashboard.counts.awaiting_confirmation).toBe(1);
    expect(JSON.stringify(dashboard)).not.toContain("phone");
  });

  it("validates a team directory without staff contacts", () => {
    const team = teamSnapshotSchema.parse({
      tenant_id: "d0000000-0000-0000-0000-000000000001",
      can_manage: true,
      members: [
        {
          user_id: "d5000000-0000-0000-0000-000000000001",
          display_name: "Synthetic owner",
          role: "owner",
          state: "active",
          is_current_user: true,
          created_at: "2030-01-01T12:00:00+00:00",
          updated_at: "2030-01-01T12:00:00+00:00",
        },
      ],
    });
    expect(team.members[0]?.role).toBe("owner");
    expect(
      teamSnapshotSchema.safeParse({
        ...team,
        members: [{ ...team.members[0], phone_e164: "+70000000000" }],
      }).success,
    ).toBe(false);
  });

  it("validates one-time invitation contracts without accepting credential fields", () => {
    const member = {
      user_id: "d5000000-0000-0000-0000-000000000009",
      display_name: "Новый оператор",
      role: "operator",
      state: "invited",
      is_current_user: false,
      created_at: "2030-01-01T12:00:00+00:00",
      updated_at: "2030-01-01T12:00:00+00:00",
    };
    expect(
      staffInvitationCreatedSchema.parse({
        invitation_id: "d6000000-0000-0000-0000-000000000009",
        invitation_token: `pli_${"a".repeat(43)}`,
        expires_at: "2030-01-03T12:00:00+00:00",
        member,
      }).member.state,
    ).toBe("invited");
    expect(
      staffInvitationSummarySchema.safeParse({
        invitation_id: "d6000000-0000-0000-0000-000000000009",
        user_id: member.user_id,
        state: "pending",
        expires_at: "2030-01-03T12:00:00+00:00",
        created_at: "2030-01-01T12:00:00+00:00",
        password_hash: "must-not-cross-boundary",
      }).success,
    ).toBe(false);
  });

  it("validates privacy-minimized staff session contracts", () => {
    expect(
      staffSessionSummarySchema.parse({
        session_id: "d7000000-0000-0000-0000-000000000001",
        client_label: "Safari · macOS",
        created_at: "2030-01-01T12:00:00+00:00",
        last_seen_at: "2030-01-01T12:05:00+00:00",
        expires_at: "2030-01-02T00:00:00+00:00",
        is_current: true,
      }).client_label,
    ).toBe("Safari · macOS");
    expect(
      staffSessionSummarySchema.safeParse({
        session_id: "d7000000-0000-0000-0000-000000000001",
        client_label: "Safari · macOS",
        created_at: "2030-01-01T12:00:00+00:00",
        last_seen_at: "2030-01-01T12:05:00+00:00",
        expires_at: "2030-01-02T00:00:00+00:00",
        is_current: true,
        token_hash: "must-not-cross-boundary",
      }).success,
    ).toBe(false);
    expect(staffSessionRevokedSchema.parse({ revoked: true, revoked_current: false })).toEqual({
      revoked: true,
      revoked_current: false,
    });
  });
});
