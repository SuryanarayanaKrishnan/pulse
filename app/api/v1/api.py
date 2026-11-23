from fastapi import APIRouter

from app.api.v1.routers import accounts, assets, budgets, ingest, me, merchant_rules, splits, transactions

api_router = APIRouter()
for module in (me, accounts, transactions, budgets, assets, splits, ingest, merchant_rules):
    api_router.include_router(module.router)
