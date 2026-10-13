import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Photolamp — сервис аренды фотоламп", template: "%s · Photolamp" },
  description:
    "Фотолампа домой: понятный процесс аренды, онлайн или по телефону. Демонстрация будущего сервиса.",
  robots: { index: false, follow: false },
  openGraph: {
    title: "Photolamp",
    description:
      "Меньше хлопот, больше времени рядом. Демонстрация будущего сервиса аренды фотоламп.",
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
