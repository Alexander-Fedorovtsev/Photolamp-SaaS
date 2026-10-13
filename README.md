# Photolamp-SaaS

Multi-tenant SaaS-платформа для аренды фотоламп и другого медицинского/домашнего оборудования.

## Цель проекта

Сделать продукт двух уровней одновременно:

1. **Собственный сервис аренды фотоламп** для пилота в Уфе.
2. **Коммерческую SaaS-платформу** для других арендодателей из Уфы и других городов РФ.

Основной канал заказов — сайт. Дополнительный обязательный канал — оформление заказа оператором **по телефону**.

## Главные продуктовые принципы

- mobile-first;
- простой заказ за 1–2 минуты;
- авторизация клиента по SMS-коду;
- единая система учёта для заказов с сайта и по телефону;
- multi-tenant архитектура с первого коммита;
- безопасная изоляция данных арендаторов;
- мягкий, доброжелательный, внушающий доверие визуальный стиль;
- юридически осторожные формулировки: сервис не ставит диагноз и не заменяет врача.

## Документация

### Архитектура и бизнес-логика
- [Основное техническое задание SaaS](docs/architecture/saas-tz.md)

### Product / Design
- [Спецификация продающей главной страницы](docs/product/landing-page-spec.md)
- [Позиционирование, офферы и тексты](docs/product/brand-messaging.md)

### Codex instructions
- [Краткий бриф для Codex](docs/codex/CODEX_BRIEF.md)
- [План реализации по этапам](docs/codex/IMPLEMENTATION_PLAN.md)
- [Готовые промты для Codex](docs/codex/PROMPTS.md)

## Рекомендуемый стек

- **Frontend:** Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui
- **Backend:** FastAPI, Python 3.12, SQLAlchemy 2, Pydantic v2
- **DB:** PostgreSQL
- **Queue / jobs:** Redis + worker
- **Infra:** Docker Compose (MVP), Traefik/Caddy or Nginx, object storage compatible with S3

## Рабочая версия: landing + tenant catalog admin

Создана воспроизводимая основа: Next.js 16 + strict TypeScript, FastAPI/Python 3.12,
SQLAlchemy, PostgreSQL 16, Redis 7, Alembic, Docker Compose, lint/format/typecheck,
unit/integration/browser tests и GitHub Actions CI.

```bash
cp .env.example .env
docker compose up --build --wait --wait-timeout 180
python3 tests/e2e/smoke.py
```

Главная: <http://localhost:3000>. Демонстрационные витрины:
<http://localhost:3000/r/demo-ufa> и <http://localhost:3000/r/demo-kazan>.
Панель синтетического арендодателя: <http://localhost:3000/admin>.
API/OpenAPI: <http://localhost:8000/docs>.

Только вымышленные публичные бренды; оформление заказа, SMS, платежи и телефон
оператора недоступны. Реализованы users/tenants/memberships, opaque session lookup,
отдельная least-privilege роль PostgreSQL и FORCE RLS. Environment-gated demo-вход
выдаёт короткую opaque session только синтетическому staff user; реальный SMS/OTP
вход ещё не реализован. В адаптивной `/admin` можно управлять tenant-scoped
филиалами, моделями, физическими приборами и тарифами; изменения аудируются и
отражаются на demo-витрине. Проект не обрабатывает реальные персональные данные,
не подтверждает доступность и не принимает заказы. Главная содержит полноценный
адаптивный landing с лёгкой SVG-анимацией.

- [Локальный запуск, проверки и облачная сборка](docs/development.md)
- [Архитектурные решения и контракт tenant isolation/RLS](docs/architecture/adr-0001-foundation.md)
- [Реализованная tenant identity и RLS-модель](docs/architecture/adr-0002-tenant-identity-rls.md)
- [Tenant-каталог, инвентарь и публичная read-model](docs/architecture/adr-0003-catalog-inventory.md)
- [Demo staff session, catalog CRUD и аудит](docs/architecture/adr-0004-demo-staff-admin.md)
- [Реализованный landing и витрина](docs/product/landing-foundation.md)
- [Демо photolamp-test.anliwei.pro и настройка Continuous Deployment](docs/deployment.md)

## Первый целевой результат

Codex должен уметь, опираясь на документацию в этом репозитории:

1. поднять проект локально;
2. реализовать продающую публичную витрину;
3. реализовать multi-tenant ядро;
4. реализовать заказы с сайта и по телефону;
5. реализовать админ-панель арендодателя;
6. обеспечить тестируемую и безопасную основу для дальнейшего масштабирования.
