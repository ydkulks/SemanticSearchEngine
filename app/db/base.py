from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class InitResult:
    tables_created: bool
    message: str


class BaseDBInitializer(ABC):
    @abstractmethod
    def create_tables(self) -> bool:
        pass

    @abstractmethod
    def check_connection(self) -> bool:
        pass

    def initialize(self) -> InitResult:
        tables_created = self.create_tables()

        return InitResult(
            tables_created=tables_created,
            message=f"Tables {'created' if tables_created else 'already exist'}",
        )
