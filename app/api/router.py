from fastapi import APIRouter

from app.api.endpoints import search, health, neo4j_search

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(search.router, tags=["search"])
api_router.include_router(neo4j_search.router, tags=["neo4j"])
