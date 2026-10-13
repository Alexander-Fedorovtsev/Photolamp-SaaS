import type { Metadata } from "next";
import { AdminWorkspace } from "@/components/admin-workspace";

export const metadata: Metadata = {
  title: "Панель арендодателя",
  description: "Демонстрационная панель управления заказами и tenant-каталогом Photolamp.",
};

export default function AdminPage() {
  return <AdminWorkspace />;
}
