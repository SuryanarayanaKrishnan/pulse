from fastapi import APIRouter

from app.api.v1.routers import (
    accounts,
    assets,
    budgets,
    cards,
    categories,
    contacts,
    email_connections,
    ingest,
    me,
    merchant_rules,
    rewards,
    rules,
    salary,
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
    cards,
    categories,
    contacts,
    email_connections,
    assets,
    splits,
    ingest,
    merchant_rules,
    rewards,
    rules,
    salary,
    statements,
    statement_passwords,
    email_connections,
):
    api_router.include_router(module.router)
