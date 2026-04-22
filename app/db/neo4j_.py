from typing import Generator
from neo4j import GraphDatabase, Driver

from app.config import settings


class Neo4jConnection:
    def __init__(self):
        self._driver: Driver | None = None

    def connect(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.neo4j.uri,
                auth=(settings.neo4j.username, settings.neo4j.password)
            )
        return self._driver

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def verify_connectivity(self) -> bool:
        try:
            with self.connect().session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False


neo4j_conn = Neo4jConnection()


def get_neo4j_session() -> Generator:
    session = neo4j_conn.connect().session()
    try:
        yield session
    finally:
        session.close()