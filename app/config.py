from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class MSSQLConfig(BaseSettings):
    host: str = ""
    port: str = "1433"
    database: str = ""
    username: str = ""
    password: str = ""
    driver: str = "ODBC Driver 18 for SQL Server"

    @property
    def connection_url(self) -> str:
        return (
            f"mssql+pyodbc://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
            f"?driver={self.driver.replace(' ', '+')}"
            f"&TrustServerCertificate=yes"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MSSQL_",
        extra="ignore",
    )


class Neo4jConfig(BaseSettings):
    uri: str = ""
    username: str = ""
    password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEO4J_",
        extra="ignore",
    )


class HBaseConfig(BaseSettings):
    host: str = ""
    port: str = "9090"
    timeout: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HBASE_",
        extra="ignore",
    )


class AppConfig(BaseSettings):
    app_name: str = "Semantic Search Engine"
    debug: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


class Settings(BaseSettings):
    app: AppConfig = AppConfig()
    mssql: MSSQLConfig = MSSQLConfig()
    neo4j: Neo4jConfig = Neo4jConfig()
    hbase: HBaseConfig = HBaseConfig()

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
