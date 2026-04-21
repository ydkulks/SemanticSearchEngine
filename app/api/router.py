from fastapi import APIRouter

from app.api.endpoints import search, health

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(search.router, tags=["search"])
