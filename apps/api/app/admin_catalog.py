"""Tenant-scoped staff catalog commands and read model."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.audit import record_audit
from app.database import TenantTransaction

StaffRole = Literal["owner", "admin", "operator", "courier"]
PublicationState = Literal["draft", "published", "archived"]
AssetState = Literal["available", "rented", "maintenance", "inspection", "retired"]
PricingState = Literal["draft", "active", "retired"]
IncludedItems = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=100)]],
    Field(min_length=1, max_length=12),
]


class Branch(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    city: str
    timezone: str
    pickup_enabled: bool
    delivery_enabled: bool
    state: Literal["active", "inactive"]


class EquipmentModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    manufacturer: str | None
    description: str
    included_items: list[str]
    illustration: Literal["floor", "compact", "mobile"]
    publication_state: PublicationState


class Asset(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    branch_id: UUID
    branch_name: str
    equipment_model_id: UUID
    model_name: str
    asset_code: str
    serial_no: str | None
    state: AssetState


class PricingRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    equipment_model_id: UUID
    model_name: str
    amount_minor_per_day: int
    deposit_minor: int
    currency: Literal["RUB"]
    minimum_days: int
    maximum_days: int
    state: PricingState
    is_demo: bool


class AssetBlock(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    asset_id: UUID
    asset_code: str
    start_date: date
    end_date: date
    reason: Literal["maintenance", "inspection", "manual"]
    note: str | None


class CatalogAdminSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: UUID
    role: StaffRole
    branches: list[Branch]
    models: list[EquipmentModel]
    assets: list[Asset]
    pricing: list[PricingRule]
    asset_blocks: list[AssetBlock]


class BranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)]
    city: Annotated[str, Field(min_length=1, max_length=100)]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]
    pickup_enabled: bool = True
    delivery_enabled: bool = True


class BranchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    pickup_enabled: bool | None = None
    delivery_enabled: bool | None = None
    state: Literal["active", "inactive"] | None = None


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=120)]
    manufacturer: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    description: Annotated[str, Field(min_length=1, max_length=500)]
    included_items: IncludedItems
    illustration: Literal["floor", "compact", "mobile"] = "floor"
    publication_state: PublicationState = "draft"


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    manufacturer: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    description: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    included_items: IncludedItems | None = None
    illustration: Literal["floor", "compact", "mobile"] | None = None
    publication_state: PublicationState | None = None


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: UUID
    equipment_model_id: UUID
    asset_code: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,31}$")]
    serial_no: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    state: AssetState = "available"


class AssetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: UUID | None = None
    equipment_model_id: UUID | None = None
    asset_code: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,31}$")] | None = None
    state: AssetState | None = None


class PricingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_model_id: UUID
    amount_minor_per_day: Annotated[int, Field(gt=0, le=10_000_000)]
    deposit_minor: Annotated[int, Field(ge=0, le=100_000_000)] = 0
    minimum_days: Annotated[int, Field(ge=1, le=365)] = 1
    maximum_days: Annotated[int, Field(ge=1, le=365)] = 30
    state: PricingState = "draft"


class PricingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_minor_per_day: Annotated[int, Field(gt=0, le=10_000_000)] | None = None
    deposit_minor: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    minimum_days: Annotated[int, Field(ge=1, le=365)] | None = None
    maximum_days: Annotated[int, Field(ge=1, le=365)] | None = None
    state: PricingState | None = None


class AssetBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    reason: Literal["maintenance", "inspection", "manual"]
    note: Annotated[str, Field(min_length=1, max_length=240)] | None = None


def require_catalog_editor(transaction: TenantTransaction) -> None:
    if transaction.role not in {"owner", "admin"}:
        raise CatalogPermissionDenied


def require_catalog_reader(transaction: TenantTransaction) -> None:
    if transaction.role not in {"owner", "admin", "operator"}:
        raise CatalogPermissionDenied


async def load_admin_catalog(transaction: TenantTransaction) -> CatalogAdminSnapshot:
    require_catalog_reader(transaction)
    session = transaction.session
    branches = await session.execute(
        text(
            "SELECT id, name, city, timezone, pickup_enabled, delivery_enabled, state "
            "FROM branches ORDER BY name"
        )
    )
    models = await session.execute(
        text(
            "SELECT id, name, manufacturer, description, included_items, illustration, "
            "publication_state FROM equipment_models ORDER BY name"
        )
    )
    pricing = await session.execute(
        text(
            "SELECT p.id, p.equipment_model_id, m.name AS model_name, "
            "p.amount_minor_per_day, p.deposit_minor, p.currency, p.minimum_days, "
            "p.maximum_days, p.state, p.is_demo FROM pricing_rules p "
            "JOIN equipment_models m ON m.id = p.equipment_model_id "
            "ORDER BY m.name, p.created_at"
        )
    )
    assets: list[Asset] = []
    blocks: list[AssetBlock] = []
    if transaction.role in {"owner", "admin"}:
        asset_rows = await session.execute(
            text(
                "SELECT a.id, a.branch_id, b.name AS branch_name, a.equipment_model_id, "
                "m.name AS model_name, a.asset_code, a.serial_no, a.state "
                "FROM assets a JOIN branches b ON b.id = a.branch_id "
                "JOIN equipment_models m ON m.id = a.equipment_model_id "
                "ORDER BY a.asset_code"
            )
        )
        block_rows = await session.execute(
            text(
                "SELECT ab.id, ab.asset_id, a.asset_code, ab.start_date, ab.end_date, "
                "ab.reason, ab.note FROM asset_blocks ab "
                "JOIN assets a ON a.id = ab.asset_id "
                "ORDER BY ab.start_date, a.asset_code"
            )
        )
        assets = [Asset.model_validate(dict(row._mapping)) for row in asset_rows]
        blocks = [AssetBlock.model_validate(dict(row._mapping)) for row in block_rows]
    return CatalogAdminSnapshot(
        tenant_id=transaction.tenant_id,
        role=transaction.role,
        branches=[Branch.model_validate(dict(row._mapping)) for row in branches],
        models=[EquipmentModel.model_validate(dict(row._mapping)) for row in models],
        assets=assets,
        pricing=[PricingRule.model_validate(dict(row._mapping)) for row in pricing],
        asset_blocks=blocks,
    )


async def create_branch(transaction: TenantTransaction, command: BranchCreate) -> UUID:
    require_catalog_editor(transaction)
    entity_id = uuid4()
    values = command.model_dump()
    await transaction.session.execute(
        text(
            "INSERT INTO branches (id, tenant_id, name, city, timezone, "
            "pickup_enabled, delivery_enabled) VALUES (:id, :tenant_id, :name, :city, "
            ":timezone, :pickup_enabled, :delivery_enabled)"
        ),
        {"id": entity_id, "tenant_id": transaction.tenant_id, **values},
    )
    await record_audit(transaction, "branch.created", "branch", entity_id, values)
    return entity_id


async def create_model(transaction: TenantTransaction, command: ModelCreate) -> UUID:
    require_catalog_editor(transaction)
    entity_id = uuid4()
    values = command.model_dump()
    await transaction.session.execute(
        text(
            "INSERT INTO equipment_models (id, tenant_id, name, category, manufacturer, "
            "description, included_items, illustration, publication_state) VALUES "
            "(:id, :tenant_id, :name, 'phototherapy_lamp', :manufacturer, :description, "
            ":included_items, :illustration, :publication_state)"
        ),
        {"id": entity_id, "tenant_id": transaction.tenant_id, **values},
    )
    await record_audit(transaction, "model.created", "equipment_model", entity_id, values)
    return entity_id


async def create_asset(transaction: TenantTransaction, command: AssetCreate) -> UUID:
    require_catalog_editor(transaction)
    entity_id = uuid4()
    values = command.model_dump()
    await transaction.session.execute(
        text(
            "INSERT INTO assets (id, tenant_id, branch_id, equipment_model_id, "
            "asset_code, serial_no, state) VALUES (:id, :tenant_id, :branch_id, "
            ":equipment_model_id, :asset_code, :serial_no, :state)"
        ),
        {"id": entity_id, "tenant_id": transaction.tenant_id, **values},
    )
    await record_audit(transaction, "asset.created", "asset", entity_id, values)
    return entity_id


async def create_pricing(transaction: TenantTransaction, command: PricingCreate) -> UUID:
    require_catalog_editor(transaction)
    if command.maximum_days < command.minimum_days:
        raise InvalidCatalogCommand("maximum_days must be greater than or equal to minimum_days")
    entity_id = uuid4()
    values = command.model_dump()
    await transaction.session.execute(
        text(
            "INSERT INTO pricing_rules (id, tenant_id, equipment_model_id, "
            "amount_minor_per_day, deposit_minor, minimum_days, maximum_days, state, "
            "is_demo) VALUES (:id, :tenant_id, :equipment_model_id, "
            ":amount_minor_per_day, :deposit_minor, :minimum_days, :maximum_days, "
            ":state, true)"
        ),
        {"id": entity_id, "tenant_id": transaction.tenant_id, **values},
    )
    await record_audit(transaction, "pricing.created", "pricing_rule", entity_id, values)
    return entity_id


async def create_asset_block(
    transaction: TenantTransaction, asset_id: UUID, command: AssetBlockCreate
) -> UUID:
    require_catalog_editor(transaction)
    if command.end_date < command.start_date:
        raise InvalidCatalogCommand("end_date must not be before start_date")
    entity_id = uuid4()
    values = command.model_dump()
    result = await transaction.session.execute(
        text(
            "INSERT INTO asset_blocks "
            "(id, tenant_id, asset_id, start_date, end_date, reason, note) "
            "SELECT :id, :tenant_id, id, :start_date, :end_date, :reason, :note "
            "FROM assets WHERE id = :asset_id RETURNING id"
        ),
        {
            "id": entity_id,
            "tenant_id": transaction.tenant_id,
            "asset_id": asset_id,
            **values,
        },
    )
    if result.scalar_one_or_none() is None:
        raise CatalogEntityNotFound
    await record_audit(transaction, "asset_block.created", "asset_block", entity_id, values)
    return entity_id


async def delete_asset_block(transaction: TenantTransaction, entity_id: UUID) -> None:
    require_catalog_editor(transaction)
    result = await transaction.session.execute(
        text("DELETE FROM asset_blocks WHERE id = :entity_id RETURNING id"),
        {"entity_id": entity_id},
    )
    if result.scalar_one_or_none() is None:
        raise CatalogEntityNotFound
    await record_audit(transaction, "asset_block.deleted", "asset_block", entity_id, {})


async def _patch_entity(
    transaction: TenantTransaction,
    *,
    table: str,
    entity_type: str,
    entity_id: UUID,
    values: dict[str, object],
) -> None:
    require_catalog_editor(transaction)
    changes = {key: value for key, value in values.items() if value is not None}
    if not changes:
        raise InvalidCatalogCommand("At least one field must be supplied")
    assignments = ", ".join(f"{field} = :{field}" for field in changes)
    result = await transaction.session.execute(
        # Table and field names only come from endpoint-owned allowlists.
        text(
            f"UPDATE {table} SET {assignments}, updated_at = now() "  # noqa: S608
            "WHERE id = :entity_id RETURNING id"
        ),
        {"entity_id": entity_id, **changes},
    )
    if result.scalar_one_or_none() is None:
        raise CatalogEntityNotFound
    await record_audit(transaction, f"{entity_type}.updated", entity_type, entity_id, changes)


async def patch_branch(
    transaction: TenantTransaction, entity_id: UUID, command: BranchPatch
) -> None:
    await _patch_entity(
        transaction,
        table="branches",
        entity_type="branch",
        entity_id=entity_id,
        values=command.model_dump(exclude_unset=True),
    )


async def patch_model(transaction: TenantTransaction, entity_id: UUID, command: ModelPatch) -> None:
    await _patch_entity(
        transaction,
        table="equipment_models",
        entity_type="equipment_model",
        entity_id=entity_id,
        values=command.model_dump(exclude_unset=True),
    )


async def patch_asset(transaction: TenantTransaction, entity_id: UUID, command: AssetPatch) -> None:
    require_catalog_editor(transaction)
    controlled_by_order = await transaction.session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM rental_allocations ra JOIN orders o "
            "ON o.tenant_id = ra.tenant_id AND o.id = ra.order_id "
            "WHERE ra.tenant_id = :tenant_id AND ra.asset_id = :asset_id "
            "AND ra.state = 'confirmed' "
            "AND o.state IN ('confirmed', 'in_progress', 'returned'))"
        ),
        {"tenant_id": transaction.tenant_id, "asset_id": entity_id},
    )
    if controlled_by_order:
        raise InvalidCatalogCommand("Asset state and assignment are controlled by its active order")
    await _patch_entity(
        transaction,
        table="assets",
        entity_type="asset",
        entity_id=entity_id,
        values=command.model_dump(exclude_unset=True),
    )


async def patch_pricing(
    transaction: TenantTransaction, entity_id: UUID, command: PricingPatch
) -> None:
    values = command.model_dump(exclude_unset=True)
    minimum = values.get("minimum_days")
    maximum = values.get("maximum_days")
    if minimum is not None and maximum is not None and maximum < minimum:
        raise InvalidCatalogCommand("maximum_days must be greater than or equal to minimum_days")
    await _patch_entity(
        transaction,
        table="pricing_rules",
        entity_type="pricing_rule",
        entity_id=entity_id,
        values=values,
    )


class CatalogPermissionDenied(Exception):
    pass


class CatalogEntityNotFound(Exception):
    pass


class InvalidCatalogCommand(Exception):
    pass
