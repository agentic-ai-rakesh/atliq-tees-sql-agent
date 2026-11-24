"""Tools the SQL agent can call. Built per request around a read-only engine."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from sqlalchemy.engine import Engine

from tshirt_agent.database import QueryError, describe_tables, run_query
from tshirt_agent.sql_guard import UnsafeQueryError

logger = logging.getLogger(__name__)


def make_tools(engine: Engine, max_rows: int, timeout_seconds: int) -> list[BaseTool]:
    schema = {t.name: t for t in describe_tables(engine)}

    @tool
    def list_tables() -> str:
        """List the database tables with their row counts."""
        return "\n".join(f"{t.name}: {t.row_count} rows" for t in schema.values())

    @tool
    def describe_table(table_name: Annotated[str, "A table name from list_tables, e.g. t_shirts."]) -> str:
        """Columns (name, type, primary key, allowed values), foreign keys and row count of a table."""
        info = schema.get((table_name or "").strip())
        if info is None:
            return f"Unknown table {table_name!r}. Tables: {', '.join(schema)}."
        return json.dumps(
            {"table": info.name, "rows": info.row_count, "columns": info.columns, "foreign_keys": info.foreign_keys},
            ensure_ascii=False,
        )

    @tool(response_format="content_and_artifact")
    def run_sql(query: Annotated[str, "One read-only SELECT statement."]) -> tuple[str, dict]:
        """Run one read-only SELECT and return its rows as JSON (errors are returned so you can fix the query)."""
        try:
            result = run_query(engine, query, max_rows, timeout_seconds)
        except (UnsafeQueryError, QueryError) as error:
            logger.info("run_sql refused/failed: %s", error)
            return f"ERROR: {error}", {"sql": query, "ok": False, "error": str(error)}
        payload = {"columns": result.columns, "rows": result.rows, "truncated": result.truncated}
        artifact = {"sql": result.sql, "ok": True, "elapsed_ms": result.elapsed_ms, **payload}
        note = f"\n(only the first {max_rows} rows are shown)" if result.truncated else ""
        return json.dumps(payload, default=str) + note, artifact

    return [list_tables, describe_table, run_sql]
