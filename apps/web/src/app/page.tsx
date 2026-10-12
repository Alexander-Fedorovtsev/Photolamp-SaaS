import Link from "next/link";
import { BrandHeader } from "@/components/brand-header";
import { LampScene } from "@/components/lamp-scene";
import { ArrowIcon } from "@/components/arrow-icon";

export default function Home() {
  return (
    <>
      <BrandHeader />
      <main id="main-content" className="container">
        <section className="hero" aria-labelledby="home-title">
          <div>
            <p className="eyebrow">Photolamp · проект в разработке</p>
            <h1 id="home-title">
              Аренда с заботой.
              <br />
              Сервис с порядком.
            </h1>
            <p className="lead">
              Готовим платформу, где семьям удобно арендовать фотолампу, а независимым арендодателям
              — управлять своим сервисом.
            </p>
            <Link href="/r/demo-ufa" className="button primary">
              Посмотреть демо в Уфе <ArrowIcon />
            </Link>
            <Link href="/r/demo-kazan" className="text-link">
              Другая организация · демо в Казани
            </Link>
            <p className="medical-note">
              Демонстрационные данные. Заказы, SMS и платежи пока недоступны.
            </p>
          </div>
          <LampScene />
        </section>
        <section className="foundation-note" aria-labelledby="foundation-title">
          <h2 id="foundation-title">Одна платформа. Свой сервис у каждого.</h2>
          <p>
            У каждой организации будут собственные оборудование, сотрудники, тарифы и заказы. Онлайн
            и по телефону — в одной системе.
          </p>
        </section>
      </main>
      <footer className="site-footer container">
        <span>Photolamp</span>
        <span>Первый этап · основа платформы</span>
      </footer>
    </>
  );
}
