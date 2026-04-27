from fastapi import APIRouter

from app.api.endpoints import search, health, neo4j_search, mssql, hbase

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(search.router, tags=["search"])
api_router.include_router(neo4j_search.router, tags=["neo4j"])
api_router.include_router(mssql.router, prefix="/mssql", tags=["mssql"])
api_router.include_router(hbase.router, prefix="/hbase", tags=["hbase"])