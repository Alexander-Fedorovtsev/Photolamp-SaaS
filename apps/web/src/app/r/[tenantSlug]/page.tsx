import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { StorefrontPreview } from "@/components/storefront-preview";
import { apiInternalUrl } from "@/lib/api";
import { getTenantCatalog } from "@/lib/tenant";

type Props = { params: Promise<{ tenantSlug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { tenantSlug } = await params;
  return {
    title: `Демонстрационная витрина ${tenantSlug}`,
    robots: { index: false, follow: false },
  };
}

export default async function Storefront({ params }: Props) {
  const { tenantSlug } = await params;
  const tenant = await getTenantCatalog(tenantSlug, apiInternalUrl());
  if (!tenant) notFound();
  return <StorefrontPreview tenant={tenant} />;
}
