from fastapi import APIRouter

from app.api.v1.routers import (
    accounts,
    assets,
    budgets,
    categories,
    email_connections,
    ingest,
    me,
    merchant_rules,
    splits,
    statement_passwords,
    statements,
    transactions,
)

api_router = APIRouter()
for module in (
    me,
    accounts,
    transactions,
    budgets,
    categories,
    email_connections,
    assets,
    splits,
    ingest,
    merchant_rules,
    statements,
    statement_passwords,
    email_connections,
):
    api_router.include_router(module.router)
