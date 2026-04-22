from app.api.dto.search_dto import (
    SearchRequestDTO,
    PaperResultDTO,
    SearchResponseDTO,
    HealthResponseDTO,
    SourceInfoDTO,
    SourcesResponseDTO,
)

from app.api.dto.mssql_dto import (
    MSSQLSearchRequestDTO,
    MSSQLPaperResultDTO,
    MSSQLSearchResponseDTO,
)

from app.api.dto.neo4j_dto import (
    Neo4jSearchRequestDTO,
    Neo4jPaperResultDTO,
    Neo4jSearchResponseDTO,
)

__all__ = [
    # Common
    "SearchRequestDTO",
    "PaperResultDTO",
    "SearchResponseDTO",
    "HealthResponseDTO",
    "SourceInfoDTO",
    "SourcesResponseDTO",
    # MSSQL
    "MSSQLSearchRequestDTO",
    "MSSQLPaperResultDTO",
    "MSSQLSearchResponseDTO",
    # Neo4j
    "Neo4jSearchRequestDTO",
    "Neo4jPaperResultDTO",
    "Neo4jSearchResponseDTO",
]