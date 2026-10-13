"""Tenant catalog, inventory, pricing and synthetic public storefronts.

Revision ID: 0003_catalog_inventory
Revises: 0002_multitenant_foundation
"""

from alembic import op

revision = "0003_catalog_inventory"
down_revision = "0002_multitenant_foundation"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE storefront_profiles (
          tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
          city text NOT NULL CHECK (length(city) BETWEEN 1 AND 100),
          accent text NOT NULL CHECK (accent IN ('sun', 'mint')),
          is_published boolean NOT NULL DEFAULT false,
          is_demo boolean NOT NULL DEFAULT false,
          booking_enabled boolean NOT NULL DEFAULT false,
          public_note text NOT NULL CHECK (length(public_note) BETWEEN 1 AND 240),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        -- statement
        CREATE TABLE branches (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
          city text NOT NULL CHECK (length(city) BETWEEN 1 AND 100),
          timezone text NOT NULL,
          pickup_enabled boolean NOT NULL DEFAULT true,
          delivery_enabled boolean NOT NULL DEFAULT true,
          state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'inactive')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id)
        );
        -- statement
        CREATE TABLE equipment_models (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
          category text NOT NULL CHECK (category IN ('phototherapy_lamp')),
          manufacturer text CHECK (manufacturer IS NULL OR length(manufacturer) BETWEEN 1 AND 120),
          description text NOT NULL CHECK (length(description) BETWEEN 1 AND 500),
          included_items text[] NOT NULL DEFAULT '{}'
            CHECK (cardinality(included_items) BETWEEN 1 AND 12),
          illustration text NOT NULL CHECK (illustration IN ('floor', 'compact', 'mobile')),
          publication_state text NOT NULL DEFAULT 'draft'
            CHECK (publication_state IN ('draft', 'published', 'archived')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, name)
        );
        -- statement
        CREATE TABLE assets (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          branch_id uuid NOT NULL,
          equipment_model_id uuid NOT NULL,
          asset_code text NOT NULL CHECK (asset_code ~ '^[A-Z0-9][A-Z0-9-]{1,31}$'),
          serial_no text,
          state text NOT NULL DEFAULT 'available'
            CHECK (state IN ('available', 'rented', 'maintenance', 'inspection', 'retired')),
          next_inspection_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, asset_code),
          FOREIGN KEY (tenant_id, branch_id) REFERENCES branches(tenant_id, id),
          FOREIGN KEY (tenant_id, equipment_model_id)
            REFERENCES equipment_models(tenant_id, id)
        );
        -- statement
        CREATE INDEX ix_assets_tenant_branch_state ON assets (tenant_id, branch_id, state);
        -- statement
        CREATE INDEX ix_assets_tenant_model_state
          ON assets (tenant_id, equipment_model_id, state);
        -- statement
        CREATE TABLE pricing_rules (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          equipment_model_id uuid NOT NULL,
          amount_minor_per_day integer NOT NULL CHECK (amount_minor_per_day > 0),
          deposit_minor integer NOT NULL DEFAULT 0 CHECK (deposit_minor >= 0),
          currency text NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
          minimum_days integer NOT NULL DEFAULT 1 CHECK (minimum_days BETWEEN 1 AND 365),
          maximum_days integer NOT NULL DEFAULT 30
            CHECK (maximum_days BETWEEN minimum_days AND 365),
          state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'active', 'retired')),
          is_demo boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          FOREIGN KEY (tenant_id, equipment_model_id)
            REFERENCES equipment_models(tenant_id, id)
        );
        -- statement
        CREATE UNIQUE INDEX uq_pricing_rules_active_model
          ON pricing_rules (tenant_id, equipment_model_id) WHERE state = 'active';

        -- statement
        ALTER TABLE storefront_profiles ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE storefront_profiles FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON storefront_profiles
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE branches ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE branches FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON branches
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE equipment_models ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE equipment_models FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON equipment_models
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE assets FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON assets
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE pricing_rules ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE pricing_rules FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON pricing_rules
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

        -- statement
        CREATE FUNCTION resolve_public_storefront(p_slug text)
        RETURNS TABLE (tenant_id uuid, is_demo boolean)
        LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = pg_catalog
        AS $function$
          SELECT t.id, s.is_demo
          FROM public.tenants t
          JOIN public.storefront_profiles s ON s.tenant_id = t.id
          WHERE t.slug = p_slug
            AND t.status IN ('trial', 'active', 'past_due')
            AND s.is_published = true
        $function$;
        -- statement
        REVOKE ALL ON storefront_profiles, branches, equipment_models, assets, pricing_rules
          FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION resolve_public_storefront(text) FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE, DELETE
          ON storefront_profiles, branches, equipment_models, assets, pricing_rules
          TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION resolve_public_storefront(text) TO photolamp_runtime;

        -- statement
        INSERT INTO tenants (id, slug, display_name, timezone, status) VALUES
          ('d0000000-0000-0000-0000-000000000001', 'demo-ufa', 'Солнечный дом · демо',
           'Asia/Yekaterinburg', 'trial'),
          ('d0000000-0000-0000-0000-000000000002', 'demo-kazan', 'Бережный свет · демо',
           'Europe/Moscow', 'trial')
        ON CONFLICT (id) DO NOTHING;
        -- statement
        INSERT INTO storefront_profiles
          (tenant_id, city, accent, is_published, is_demo, booking_enabled, public_note)
        VALUES
          ('d0000000-0000-0000-0000-000000000001', 'Уфа', 'sun', true, true, false,
           'Синтетический каталог: цены и остатки приведены только для демонстрации.'),
          ('d0000000-0000-0000-0000-000000000002', 'Казань', 'mint', true, true, false,
           'Синтетический каталог: цены и остатки приведены только для демонстрации.')
        ON CONFLICT (tenant_id) DO NOTHING;
        -- statement
        INSERT INTO branches
          (id, tenant_id, name, city, timezone, pickup_enabled, delivery_enabled)
        VALUES
          ('d1000000-0000-0000-0000-000000000001',
           'd0000000-0000-0000-0000-000000000001', 'Демо-точка в Уфе', 'Уфа',
           'Asia/Yekaterinburg', true, true),
          ('d1000000-0000-0000-0000-000000000002',
           'd0000000-0000-0000-0000-000000000002', 'Демо-точка в Казани', 'Казань',
           'Europe/Moscow', true, true)
        ON CONFLICT (id) DO NOTHING;
        -- statement
        INSERT INTO equipment_models
          (id, tenant_id, name, category, description, included_items, illustration,
           publication_state)
        VALUES
          ('d2000000-0000-0000-0000-000000000001',
           'd0000000-0000-0000-0000-000000000001', 'Домашняя LED-фотолампа',
           'phototherapy_lamp',
           'Напольная модель для дома. Комплект и правила уточняются перед передачей.',
           ARRAY['лампа', 'защитный кейс', 'инструкция производителя'],
           'floor', 'published'),
          ('d2000000-0000-0000-0000-000000000002',
           'd0000000-0000-0000-0000-000000000001', 'Компактная LED-фотолампа',
           'phototherapy_lamp',
           'Компактный демо-вариант. Сервис не назначает режим применения.',
           ARRAY['лампа', 'защитный кейс', 'инструкция производителя'],
           'compact', 'published'),
          ('d2000000-0000-0000-0000-000000000003',
           'd0000000-0000-0000-0000-000000000002', 'Мобильная LED-фотолампа',
           'phototherapy_lamp',
           'Мобильная демо-модель. Использование — по назначению врача и инструкции.',
           ARRAY['лампа', 'защитный кейс', 'инструкция производителя'],
           'mobile', 'published')
        ON CONFLICT (id) DO NOTHING;
        -- statement
        INSERT INTO assets
          (id, tenant_id, branch_id, equipment_model_id, asset_code, state)
        VALUES
          ('d3000000-0000-0000-0000-000000000001',
           'd0000000-0000-0000-0000-000000000001',
           'd1000000-0000-0000-0000-000000000001',
           'd2000000-0000-0000-0000-000000000001', 'DEMO-UFA-01', 'available'),
          ('d3000000-0000-0000-0000-000000000002',
           'd0000000-0000-0000-0000-000000000001',
           'd1000000-0000-0000-0000-000000000001',
           'd2000000-0000-0000-0000-000000000001', 'DEMO-UFA-02', 'inspection'),
          ('d3000000-0000-0000-0000-000000000003',
           'd0000000-0000-0000-0000-000000000001',
           'd1000000-0000-0000-0000-000000000001',
           'd2000000-0000-0000-0000-000000000002', 'DEMO-UFA-03', 'available'),
          ('d3000000-0000-0000-0000-000000000004',
           'd0000000-0000-0000-0000-000000000002',
           'd1000000-0000-0000-0000-000000000002',
           'd2000000-0000-0000-0000-000000000003', 'DEMO-KZN-01', 'available')
        ON CONFLICT (id) DO NOTHING;
        -- statement
        INSERT INTO pricing_rules
          (id, tenant_id, equipment_model_id, amount_minor_per_day, deposit_minor,
           minimum_days, maximum_days, state, is_demo)
        VALUES
          ('d4000000-0000-0000-0000-000000000001',
           'd0000000-0000-0000-0000-000000000001',
           'd2000000-0000-0000-0000-000000000001', 49000, 0, 3, 30, 'active', true),
          ('d4000000-0000-0000-0000-000000000002',
           'd0000000-0000-0000-0000-000000000001',
           'd2000000-0000-0000-0000-000000000002', 39000, 0, 3, 30, 'active', true),
          ('d4000000-0000-0000-0000-000000000003',
           'd0000000-0000-0000-0000-000000000002',
           'd2000000-0000-0000-0000-000000000003', 45000, 0, 3, 30, 'active', true)
        ON CONFLICT (id) DO NOTHING;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP FUNCTION IF EXISTS resolve_public_storefront(text);
        -- statement
        DROP TABLE IF EXISTS pricing_rules;
        -- statement
        DROP TABLE IF EXISTS assets;
        -- statement
        DROP TABLE IF EXISTS equipment_models;
        -- statement
        DROP TABLE IF EXISTS branches;
        -- statement
        DROP TABLE IF EXISTS storefront_profiles;
        -- statement
        DELETE FROM tenants WHERE id IN (
          'd0000000-0000-0000-0000-000000000001',
          'd0000000-0000-0000-0000-000000000002'
        );
        """
    )
