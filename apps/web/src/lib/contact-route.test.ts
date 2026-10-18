import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";
import { POST } from "@/app/api/admin/orders/[orderId]/contact/route";
import { proxyStaffRequest } from "@/lib/admin-proxy";
vi.mock("@/lib/admin-proxy", () => ({ proxyStaffRequest: vi.fn() }));
const tenant = "d0000000-0000-0000-0000-000000000002";
const order = "3c97584c-f4f0-44ae-b376-cb186f1a0f7c";
describe("contact BFF boundary", () => {
  beforeEach(() => vi.mocked(proxyStaffRequest).mockReset());
  it("forwards real UUIDs and idempotency key to the authenticated audited API", async () => {
    vi.mocked(proxyStaffRequest).mockResolvedValue(NextResponse.json({}));
    await POST(
      new NextRequest(`http://localhost/api/admin/orders/${order}/contact?tenant_id=${tenant}`, {
        method: "POST",
        headers: { "Idempotency-Key": "contact-browser-regression-0001" },
      }),
      { params: Promise.resolve({ orderId: order }) },
    );
    expect(proxyStaffRequest).toHaveBeenCalledWith(
      `/api/v1/t/${tenant}/orders/${order}/contact-access`,
      { method: "POST", headers: { "Idempotency-Key": "contact-browser-regression-0001" } },
    );
  });
  it.each([
    ["invalid", order, "valid-contact-key-0001"],
    [tenant, "invalid", "valid-contact-key-0001"],
    [tenant, order, "short"],
  ])(
    "rejects malformed identifiers or key without calling upstream",
    async (tenantId, orderId, key) => {
      const response = await POST(
        new NextRequest(
          `http://localhost/api/admin/orders/${orderId}/contact?tenant_id=${tenantId}`,
          { method: "POST", headers: { "Idempotency-Key": key } },
        ),
        { params: Promise.resolve({ orderId }) },
      );
      expect(response.status).toBe(400);
      expect(proxyStaffRequest).not.toHaveBeenCalled();
    },
  );
});
