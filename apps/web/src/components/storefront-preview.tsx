import type { TenantCatalog } from "@/lib/tenant";
import { RentalLanding } from "./rental-landing";

export function StorefrontPreview({ tenant }: { tenant: TenantCatalog }) {
  return (
    <RentalLanding
      name={tenant.display_name}
      city={tenant.city}
      accent={tenant.accent}
      catalog={tenant}
    />
  );
}
