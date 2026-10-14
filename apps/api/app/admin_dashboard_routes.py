"""HTTP boundary for the tenant operations dashboard."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.admin_dashboard import OperationsDashboard, load_operations_dashboard
from app.admin_orders import OrderPermissionDenied
from app.database import TenantTransaction
from app.tenancy import authorized_tenant_transaction

router = APIRouter(prefix="/api/v1/t/{tenant_id}", tags=["tenant dashboard"])
Transaction = Annotated[TenantTransaction, Depends(authorized_tenant_transaction)]


@router.get("/dashboard", response_model=OperationsDashboard)
async def operations_dashboard(response: Response, transaction: Transaction) -> OperationsDashboard:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await load_operations_dashboard(transaction)
    except OrderPermissionDenied:
        raise HTTPException(
            status_code=403, detail="Owner, admin or operator role required"
        ) from None
