#!/usr/bin/env python
import argparse
import os
import sys

from app.db.init import MSSQLInitializer, Neo4jInitializer, HBaseInitializer
from app.db.base import BaseDBInitializer


INITIALIZERS: dict[str, type[BaseDBInitializer]] = {
    "mssql": MSSQLInitializer,
    "neo4j": Neo4jInitializer,
    "hbase": HBaseInitializer,
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_DATA_PATH = os.path.join(PROJECT_ROOT, "data", "dblp_demo.json")


def init_database(db_type: str, data_path: str | None = None) -> int:
    if db_type not in INITIALIZERS:
        print(f"Unknown database type: {db_type}")
        return 1

    initializer_class = INITIALIZERS[db_type]
    initializer = initializer_class()

    if not initializer.check_connection():
        print(f"Cannot connect to {db_type}. Check configuration.")
        return 1

    result = initializer.initialize(data_path=data_path)
    print(result.message)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Initialize database schema")
    parser.add_argument(
        "--db",
        choices=list(INITIALIZERS.keys()) + ["all"],
        default="mssql",
        help="Database type to initialize",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check connection, don't create anything",
    )
    parser.add_argument(
        "--load-data",
        action="store_true",
        help="Load DBLP dataset into MSSQL and build Neo4j graph",
    )
    parser.add_argument(
        "--drop-tables",
        action="store_true",
        help="Drop existing tables before creating",
    )
    parser.add_argument(
        "--data-path",
        default=DEFAULT_DATA_PATH,
        help="Path to DBLP JSON file",
    )

    args = parser.parse_args()

    if args.check_only:
        for db_type, initializer_class in INITIALIZERS.items():
            initializer = initializer_class()
            status = "connected" if initializer.check_connection() else "unavailable"
            print(f"{db_type}: {status}")
        return 0

    if args.drop_tables:
        if args.db == "mssql" or args.db == "all":
            print("Dropping MSSQL tables...")
            initializer = MSSQLInitializer()
            if initializer.check_connection():
                initializer.drop_tables()
        if args.db == "neo4j" or args.db == "all":
            print("Note: Neo4j graph can be cleared via Neo4j Browser (MATCH (n) DETACH DELETE n)")
        if args.db == "all":
            return 0

    data_path = args.data_path if args.load_data else None

    if args.db == "all":
        success = True
        if args.load_data:
            print("Loading DBLP data into MSSQL...")
            if init_database("mssql", data_path) != 0:
                success = False
            print("\nBuilding Neo4j citation graph...")
            if init_database("neo4j", data_path) != 0:
                success = False
            init_database("hbase", data_path)
        else:
            for db_type in INITIALIZERS.keys():
                if init_database(db_type) != 0:
                    success = False
        return 0 if success else 1

    return init_database(args.db, data_path)


if __name__ == "__main__":
    sys.exit(main())
