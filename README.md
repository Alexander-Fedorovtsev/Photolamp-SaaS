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

## Рабочая версия: landing + tenant operations

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

Только вымышленные публичные бренды; реальные SMS, платежи и production-auth
оператора недоступны. Реализованы users/tenants/memberships, opaque session lookup,
отдельная least-privilege роль PostgreSQL и FORCE RLS. Environment-gated demo-вход
выдаёт короткую opaque session только синтетическому staff user; реальный SMS/OTP
вход ещё не реализован. В адаптивной `/admin` можно просматривать tenant-scoped
очередь и карточки заказов, создавать подтверждённый заказ по телефону на
фиксированный синтетический контакт, подтверждать/отменять допустимые заявки,
проводить заказ через выдачу, возврат и осмотр, отдельно открывать зашифрованный
контакт с записью события доступа, обрабатывать запросы продления и возврата,
видеть вычисляемую просрочку, а также управлять филиалами, моделями,
физическими приборами и тарифами; изменения
аудируются и отражаются на demo-витрине. API умеет создать анонимную checkout capability,
атомарный hold, истекающий draft order и local/test OTP identity без plaintext
телефона; demo-витрины оставляют booking и настоящий SMS выключенными и не
принимают публичные заказы. Контакты телефонного заказа зашифрованы, поиск
ограничен tenant-scoped HMAC, а RLS закрывает прямой cross-tenant доступ.
Подтверждённая customer session может читать только собственную
минимизированную историю заказов и создавать запросы для активной аренды.
Задания SMS сохраняются дедуплицированно, но без провайдера остаются явно
заблокированными и не считаются отправленными. Главная содержит полноценный
адаптивный landing с лёгкой SVG-анимацией.

- [Локальный запуск, проверки и облачная сборка](docs/development.md)
- [Архитектурные решения и контракт tenant isolation/RLS](docs/architecture/adr-0001-foundation.md)
- [Реализованная tenant identity и RLS-модель](docs/architecture/adr-0002-tenant-identity-rls.md)
- [Tenant-каталог, инвентарь и публичная read-model](docs/architecture/adr-0003-catalog-inventory.md)
- [Demo staff session, catalog CRUD и аудит](docs/architecture/adr-0004-demo-staff-admin.md)
- [Анонимный checkout и истекающие draft orders](docs/architecture/adr-0007-anonymous-checkout-drafts.md)
- [Customer OTP и граница SMS-провайдера](docs/architecture/adr-0008-customer-otp-boundary.md)
- [Клиентский read model заказов](docs/architecture/adr-0009-customer-order-read-model.md)
- [Операционная очередь и переходы заказов](docs/architecture/adr-0010-staff-order-operations.md)
- [Зашифрованные контакты и заказы по телефону](docs/architecture/adr-0011-encrypted-phone-orders.md)
- [Выдача, возврат, осмотр и контролируемый доступ к контакту](docs/architecture/adr-0012-order-fulfillment.md)
- [Продления, просрочки, возврат и fail-closed уведомления](docs/architecture/adr-0013-order-requests-notifications.md)
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
