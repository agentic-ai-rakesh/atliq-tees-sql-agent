"""Read-only database access: engine, schema description and guarded query execution.

Safety layer two: every connection is read-only at the database level
(SQLite: opened with mode=ro and PRAGMA query_only; MySQL: SET SESSION TRANSACTION READ ONLY),
queries have a time limit, and results are capped at max_result_rows.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from tshirt_agent.sql_guard import validate_sql

logger = logging.getLogger(__name__)

LOW_CARDINALITY = 10  # text columns with at most this many values get them listed


class DatabaseNotReadyError(RuntimeError):
    """The database file is missing or the server cannot be reached."""


class QueryError(RuntimeError):
    """The database rejected the query (bad column, syntax, timeout, ...)."""


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: float


@dataclass
class TableInfo:
    name: str
    row_count: int
    columns: list[dict[str, Any]]
    foreign_keys: list[str] = field(default_factory=list)


def create_readonly_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "")
        if not url.database or not path.exists():
            raise DatabaseNotReadyError(f"Database file {path} not found. Run `make db` to create it.")
        engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true", connect_args={"uri": True})

        @event.listens_for(engine, "connect")
        def _sqlite_read_only(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA query_only = ON")

        return engine
    if url.get_backend_name() == "mysql":
        engine = create_engine(url, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def _mysql_read_only(dbapi_connection, _record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")

        return engine
    raise DatabaseNotReadyError(f"Unsupported database {url.get_backend_name()!r}: use sqlite or mysql+pymysql.")


def dialect_of(engine: Engine) -> str:
    return engine.dialect.name  # "sqlite" or "mysql"


def table_names(engine: Engine) -> set[str]:
    try:
        return set(inspect(engine).get_table_names())
    except SQLAlchemyError as error:
        raise DatabaseNotReadyError(f"Cannot read the database: {error.__class__.__name__}: {error}") from error


def describe_tables(engine: Engine) -> list[TableInfo]:
    """Tables, columns (with types and, for small text columns, their values), keys and row counts."""
    inspector = inspect(engine)
    tables: list[TableInfo] = []
    with engine.connect() as connection:
        for name in sorted(inspector.get_table_names()):
            quoted = engine.dialect.identifier_preparer.quote(name)
            row_count = connection.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar_one()
            primary = set(inspector.get_pk_constraint(name).get("constrained_columns") or [])
            columns = []
            for column in inspector.get_columns(name):
                info = {
                    "name": column["name"],
                    "type": str(column["type"]),
                    "primary_key": column["name"] in primary,
                }
                if "CHAR" in info["type"].upper() or "TEXT" in info["type"].upper() or "ENUM" in info["type"].upper():
                    col = engine.dialect.identifier_preparer.quote(column["name"])
                    values = (
                        connection.execute(
                            text(f"SELECT DISTINCT {col} FROM {quoted} ORDER BY {col} LIMIT {LOW_CARDINALITY + 1}")
                        )
                        .scalars()
                        .all()
                    )
                    if len(values) <= LOW_CARDINALITY:
                        info["values"] = list(values)
                columns.append(info)
            fks = [
                f"{', '.join(fk['constrained_columns'])} → {fk['referred_table']}({', '.join(fk['referred_columns'])})"
                for fk in inspector.get_foreign_keys(name)
            ]
            tables.append(TableInfo(name, row_count, columns, fks))
    return tables


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def run_query(engine: Engine, sql: str, max_rows: int, timeout_seconds: int) -> QueryResult:
    """Validate, then run one read-only SELECT with a time limit and a row cap."""
    sql = validate_sql(sql, dialect_of(engine), table_names(engine))
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            raw = connection.connection.driver_connection
            if dialect_of(engine) == "sqlite":
                deadline = time.monotonic() + timeout_seconds
                raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            else:
                connection.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_seconds * 1000}"))
            try:
                result = connection.execute(text(sql))
                columns = list(result.keys())
                fetched = result.fetchmany(max_rows + 1)
            finally:
                if dialect_of(engine) == "sqlite":
                    raw.set_progress_handler(None, 0)
    except SQLAlchemyError as error:
        message = str(getattr(error, "orig", error)).splitlines()[0]
        if "interrupted" in message.lower():
            message = f"the query took longer than {timeout_seconds}s and was stopped"
        raise QueryError(message) from error
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    rows = [[_plain(v) for v in row] for row in fetched[:max_rows]]
    logger.info("SQL ok (%d rows, %.1f ms): %s", len(rows), elapsed, " ".join(sql.split())[:200])
    return QueryResult(sql, columns, rows, len(fetched) > max_rows, elapsed)
