import type { Metadata } from "next";
import { StaffInvitationSetup } from "@/components/staff-invitation-setup";

export const metadata: Metadata = {
  title: "Подключение сотрудника — Photolamp",
  robots: { index: false, follow: false },
};

export default function StaffInvitationPage() {
  return <StaffInvitationSetup />;
}
