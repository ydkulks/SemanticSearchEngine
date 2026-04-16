from typing import Optional
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=100)
    use_reranker: bool = True
    filters: Optional[dict] = None
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class SearchResult(BaseModel):
    id: str
    content: str
    source: str = "mssql"
    score: float
    metadata: Optional[dict] = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    total: int
    latency_ms: float
    sources_queried: list[str]


class IngestRequest(BaseModel):
    content: str = Field(..., min_length=1)
    title: Optional[str] = None
    metadata: Optional[dict] = None


class IngestResponse(BaseModel):
    id: str
    message: str


class HealthResponse(BaseModel):
    status: str
    mssql: str
    neo4j: Optional[str] = None


class SourceInfo(BaseModel):
    name: str
    type: str
    status: str


class SourcesResponse(BaseModel):
    sources: list[SourceInfo]
