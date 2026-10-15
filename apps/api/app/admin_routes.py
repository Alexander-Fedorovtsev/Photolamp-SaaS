"""HTTP boundary for authorized tenant catalog administration."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.admin_catalog import (
    AssetBlockCreate,
    AssetCreate,
    AssetPatch,
    BranchCreate,
    BranchPatch,
    CatalogAdminSnapshot,
    CatalogEntityNotFound,
    CatalogPermissionDenied,
    InvalidCatalogCommand,
    ModelCreate,
    ModelPatch,
    PricingCreate,
    PricingPatch,
    create_asset,
    create_asset_block,
    create_branch,
    create_model,
    create_pricing,
    delete_asset_block,
    load_admin_catalog,
    patch_asset,
    patch_branch,
    patch_model,
    patch_pricing,
)
from app.database import TenantTransaction
from app.tenancy import authorized_tenant_transaction

router = APIRouter(prefix="/api/v1/t/{tenant_id}", tags=["tenant catalog admin"])
Transaction = Annotated[TenantTransaction, Depends(authorized_tenant_transaction)]


class EntityCreated(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID


class EntityUpdated(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    updated: Literal[True] = True


class EntityDeleted(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    deleted: Literal[True] = True


def translate_catalog_error(error: Exception) -> HTTPException:
    if isinstance(error, CatalogPermissionDenied):
        return HTTPException(status_code=403, detail="Owner or admin role required")
    if isinstance(error, CatalogEntityNotFound):
        return HTTPException(status_code=404, detail="Catalog entity not found")
    if isinstance(error, InvalidCatalogCommand):
        return HTTPException(status_code=422, detail=str(error))
    raise error


@router.get("/catalog-admin", response_model=CatalogAdminSnapshot)
async def catalog_admin(transaction: Transaction) -> CatalogAdminSnapshot:
    try:
        return await load_admin_catalog(transaction)
    except CatalogPermissionDenied:
        raise HTTPException(
            status_code=403, detail="Owner, admin or operator role required"
        ) from None


@router.post("/branches", response_model=EntityCreated, status_code=status.HTTP_201_CREATED)
async def add_branch(command: BranchCreate, transaction: Transaction) -> EntityCreated:
    try:
        return EntityCreated(id=await create_branch(transaction, command))
    except (CatalogPermissionDenied, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.patch("/branches/{entity_id}", response_model=EntityUpdated)
async def update_branch(
    entity_id: UUID, command: BranchPatch, transaction: Transaction
) -> EntityUpdated:
    try:
        await patch_branch(transaction, entity_id, command)
        return EntityUpdated(id=entity_id)
    except (CatalogPermissionDenied, CatalogEntityNotFound, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.post("/models", response_model=EntityCreated, status_code=status.HTTP_201_CREATED)
async def add_model(command: ModelCreate, transaction: Transaction) -> EntityCreated:
    try:
        return EntityCreated(id=await create_model(transaction, command))
    except (CatalogPermissionDenied, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.patch("/models/{entity_id}", response_model=EntityUpdated)
async def update_model(
    entity_id: UUID, command: ModelPatch, transaction: Transaction
) -> EntityUpdated:
    try:
        await patch_model(transaction, entity_id, command)
        return EntityUpdated(id=entity_id)
    except (CatalogPermissionDenied, CatalogEntityNotFound, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.post("/assets", response_model=EntityCreated, status_code=status.HTTP_201_CREATED)
async def add_asset(command: AssetCreate, transaction: Transaction) -> EntityCreated:
    try:
        return EntityCreated(id=await create_asset(transaction, command))
    except (CatalogPermissionDenied, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.patch("/assets/{entity_id}", response_model=EntityUpdated)
async def update_asset(
    entity_id: UUID, command: AssetPatch, transaction: Transaction
) -> EntityUpdated:
    try:
        await patch_asset(transaction, entity_id, command)
        return EntityUpdated(id=entity_id)
    except (CatalogPermissionDenied, CatalogEntityNotFound, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.post(
    "/assets/{asset_id}/blocks",
    response_model=EntityCreated,
    status_code=status.HTTP_201_CREATED,
)
async def add_asset_block(
    asset_id: UUID, command: AssetBlockCreate, transaction: Transaction
) -> EntityCreated:
    try:
        return EntityCreated(id=await create_asset_block(transaction, asset_id, command))
    except (CatalogPermissionDenied, CatalogEntityNotFound, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.delete("/asset-blocks/{entity_id}", response_model=EntityDeleted)
async def remove_asset_block(entity_id: UUID, transaction: Transaction) -> EntityDeleted:
    try:
        await delete_asset_block(transaction, entity_id)
        return EntityDeleted(id=entity_id)
    except (CatalogPermissionDenied, CatalogEntityNotFound) as error:
        raise translate_catalog_error(error) from None


@router.post("/pricing", response_model=EntityCreated, status_code=status.HTTP_201_CREATED)
async def add_pricing(command: PricingCreate, transaction: Transaction) -> EntityCreated:
    try:
        return EntityCreated(id=await create_pricing(transaction, command))
    except (CatalogPermissionDenied, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None


@router.patch("/pricing/{entity_id}", response_model=EntityUpdated)
async def update_pricing(
    entity_id: UUID, command: PricingPatch, transaction: Transaction
) -> EntityUpdated:
    try:
        await patch_pricing(transaction, entity_id, command)
        return EntityUpdated(id=entity_id)
    except (CatalogPermissionDenied, CatalogEntityNotFound, InvalidCatalogCommand) as error:
        raise translate_catalog_error(error) from None
