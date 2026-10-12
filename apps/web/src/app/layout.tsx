import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Photolamp — сервис аренды фотоламп", template: "%s · Photolamp" },
  description: "Создаём удобный сервис аренды фотоламп и платформу для независимых арендодателей.",
  robots: { index: false, follow: false },
  openGraph: {
    title: "Photolamp",
    description: "Аренда фотоламп с понятным сервисом. Проект в разработке.",
    locale: "ru_RU",
    type: "website",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>
        <a href="#main-content" className="skip-link">
          Перейти к содержимому
        </a>
        {children}
      </body>
    </html>
  );
}
