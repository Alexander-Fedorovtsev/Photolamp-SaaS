import type { TenantPreview } from "@/lib/tenant";
import { RentalLanding } from "./rental-landing";

export function StorefrontPreview({ tenant }: { tenant: TenantPreview }) {
  return <RentalLanding name={tenant.display_name} city={tenant.city} accent={tenant.accent} />;
}
