import json
import base64
from typing import Optional
from urllib.request import urlopen, Request
from urllib.parse import quote
from app.config import settings


class HBaseRESTConnection:
    def __init__(self):
        self._base_url = settings.hbase.rest_url
        self._timeout = settings.hbase.timeout

    def connect(self):
        return self

    def check_connection(self) -> bool:
        try:
            resp = urlopen(f"{self._base_url}/version", timeout=self._timeout)
            return resp.status == 200
        except Exception:
            return False

    def list_tables(self) -> list[str]:
        req = Request(
            f"{self._base_url}/",
            headers={"Accept": "application/json"},
        )
        resp = urlopen(req, timeout=self._timeout)
        data = json.loads(resp.read().decode())
        return data.get("table", [])

    def create_table(self, table_name: str, families: dict) -> bool:
        schema = {
            "name": table_name,
            "ColumnSchema": [
                {"name": family, "VERSIONS": props.get("max_versions", 1)}
                for family, props in families.items()
            ]
        }
        req = Request(
            f"{self._base_url}/{table_name}/schema",
            data=json.dumps(schema).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            urlopen(req, timeout=self._timeout)
            return True
        except Exception:
            return False

    def get_table(self, table_name: str) -> "HBaseRESTTable":
        return HBaseRESTTable(self._base_url, table_name, self._timeout)

    def delete_table(self, table_name: str) -> bool:
        req = Request(
            f"{self._base_url}/{quote(table_name)}/schema",
            method="DELETE",
        )
        try:
            urlopen(req, timeout=self._timeout)
            return True
        except Exception:
            return False


class HBaseRESTTable:
    def __init__(self, base_url: str, table_name: str, timeout: int):
        self._base_url = base_url
        self._table_name = table_name
        self._timeout = timeout
        self._url = f"{base_url}/{quote(table_name)}"

    def _encode_col(self, col) -> str:
        if isinstance(col, bytes):
            return base64.b64encode(col).decode()
        return base64.b64encode(col.encode()).decode()

    def _encode_val(self, val) -> str:
        if isinstance(val, bytes):
            return base64.b64encode(val).decode()
        return base64.b64encode(val.encode()).decode()

    def put(self, row_key: str, data: dict) -> bool:
        row_key_enc = self._encode_col(row_key)
        cell_data = {
            "Row": [
                {
                    "key": row_key_enc,
                    "Cell": [
                        {
                            "column": self._encode_col(col),
                            "$": self._encode_val(val),
                        }
                        for col, val in data.items()
                    ],
                }
            ]
        }
        req = Request(
            f"{self._url}/fakerow",
            data=json.dumps(cell_data).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            urlopen(req, timeout=self._timeout)
            return True
        except Exception:
            return False

    def put_batch(self, rows: list[tuple[str, dict]]) -> int:
        count = 0
        for row_key, data in rows:
            if self.put(row_key, data):
                count += 1
        return count

    def get(self, row_key: str, include_deleted: bool = False) -> dict:
        req = Request(
            f"{self._url}/{quote(row_key)}",
            headers={"Accept": "application/json"},
        )
        try:
            resp = urlopen(req, timeout=self._timeout)
            return json.loads(resp.read().decode())
        except Exception:
            return {}

    def row(self, row_key: str, include_deleted: bool = False) -> dict:
        data = self.get(row_key, include_deleted)
        if "Row" not in data or not data["Row"]:
            return {}
        row = data["Row"][0]
        result = {}
        for cell in row.get("Cell", []):
            col_bytes = base64.b64decode(cell["column"].encode())
            val_bytes = base64.b64decode(cell["$"].encode())
            result[col_bytes.decode()] = val_bytes.decode()
        return result

    def scan(
        self,
        start_row: Optional[str] = None,
        end_row: Optional[str] = None,
        limit: int = 100,
        columns: Optional[list[str]] = None,
    ) -> list[dict]:
        scan_spec = {}
        if start_row:
            scan_spec["startRow"] = base64.b64encode(start_row.encode()).decode()
        if end_row:
            scan_spec["stopRow"] = base64.b64encode(end_row.encode()).decode()
        if columns:
            scan_spec["columns"] = [
                base64.b64encode(c.encode()).decode() for c in columns
            ]
        scan_spec["limit"] = limit

        req = Request(
            f"{self._url}/scan",
            data=json.dumps(scan_spec).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=self._timeout)
            data = json.loads(resp.read())
            return data.get("Row", [])
        except Exception:
            return []

    def delete_row(self, row_key: str) -> bool:
        req = Request(
            f"{self._url}/{quote(row_key)}",
            method="DELETE",
        )
        try:
            urlopen(req, timeout=self._timeout)
            return True
        except Exception:
            return False

    def exists(self, row_key: str) -> bool:
        data = self.get(row_key)
        return "Row" in data and len(data["Row"]) > 0


hbase_conn = HBaseRESTConnection()


def get_hbase_table(table_name: str) -> HBaseRESTTable:
    return hbase_conn.get_table(table_name)


def with_hbase_connection():
    yield hbase_conn