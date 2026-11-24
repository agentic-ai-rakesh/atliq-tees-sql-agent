"""Read-only SQL guard: one SELECT over known tables, nothing else.

This is the first of two safety layers. The second is the database connection itself,
which is opened read-only (see database.py), so even a query that slipped past this check
could not change data.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_SQL_CHARS = 4000

_WRITE_OR_ADMIN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Into,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Grant,
    exp.Copy,
    exp.LoadData,
)
# Functions that read files, sleep or otherwise reach outside the data.
_BLOCKED_FUNCTIONS = {
    "sleep",
    "benchmark",
    "load_file",
    "load_extension",
    "readfile",
    "writefile",
    "sys_exec",
    "sys_eval",
    "pg_sleep",
    "randomblob",
}


class UnsafeQueryError(ValueError):
    """The SQL is not a single read-only SELECT over the store's tables."""


def validate_sql(sql: str, dialect: str, allowed_tables: set[str]) -> str:
    """Return the cleaned SQL, or raise UnsafeQueryError saying why it was refused."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise UnsafeQueryError("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeQueryError(f"The query is longer than {MAX_SQL_CHARS} characters.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as error:
        raise UnsafeQueryError(f"Not valid or not allowed SQL: {str(error).splitlines()[0]}") from error
    if len(statements) != 1:
        raise UnsafeQueryError("Send exactly one statement.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise UnsafeQueryError(f"Only SELECT queries are allowed, not {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, _WRITE_OR_ADMIN):
            raise UnsafeQueryError(f"{node.key.upper()} is not allowed: the database is read-only.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _BLOCKED_FUNCTIONS:
                raise UnsafeQueryError(f"The function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.db or table.catalog:
            raise UnsafeQueryError(f"Use the table name alone, without a database prefix: {table.sql()}.")
        if name not in allowed and name not in cte_names:
            raise UnsafeQueryError(
                f"Unknown table {table.name!r}. Available tables: {', '.join(sorted(allowed_tables))}."
            )
    return sql
