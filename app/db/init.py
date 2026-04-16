from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.config import settings
from app.db.base import BaseDBInitializer, InitResult
from app.models.database import Base


class MSSQLInitializer(BaseDBInitializer):
    def __init__(self):
        self._engine: Engine | None = None

    def _get_db_engine(self) -> Engine:
        return create_engine(
            settings.mssql.connection_url,
            pool_pre_ping=True,
        )

    def check_connection(self) -> bool:
        try:
            engine = self._get_db_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def create_tables(self) -> bool:
        engine = self._get_db_engine()
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        tables_to_create = ["documents", "embeddings", "search_history"]
        needs_creation = [t for t in tables_to_create if t not in existing_tables]

        if not needs_creation:
            return False

        Base.metadata.create_all(bind=engine)
        return True

    def initialize(self) -> InitResult:
        try:
            tables_created = self.create_tables()

            return InitResult(
                tables_created=tables_created,
                message=f"MSSQL: Tables {'created' if tables_created else 'already exist'}",
            )
        except Exception as e:
            return InitResult(
                tables_created=False,
                message=f"MSSQL initialization failed: {str(e)}",
            )


class Neo4jInitializer(BaseDBInitializer):
    def check_connection(self) -> bool:
        return False

    def create_tables(self) -> bool:
        return False


class HBaseInitializer(BaseDBInitializer):
    def check_connection(self) -> bool:
        return False

    def create_tables(self) -> bool:
        return False
