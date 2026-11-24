import pytest

from tshirt_agent.sql_guard import UnsafeQueryError, validate_sql

TABLES = {"t_shirts", "discounts"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(stock_quantity) FROM t_shirts WHERE brand = 'Nike'",
        "select brand, count(*) from t_shirts group by brand;",
        "WITH levi AS (SELECT * FROM t_shirts WHERE brand = 'Levi') SELECT SUM(price) FROM levi",
        "SELECT t.brand FROM t_shirts t LEFT JOIN discounts d ON d.t_shirt_id = t.t_shirt_id",
        "SELECT brand FROM t_shirts UNION SELECT 'x'",
        "SELECT (SELECT MAX(pct_discount) FROM discounts) AS top",
    ],
)
def test_read_only_selects_are_allowed(sql):
    assert validate_sql(sql, "sqlite", TABLES) == sql.strip().rstrip(";")


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("", "empty"),
        ("DELETE FROM t_shirts", "Only SELECT"),
        ("UPDATE t_shirts SET price = 1", "Only SELECT"),
        ("DROP TABLE discounts", "Only SELECT"),
        ("INSERT INTO discounts VALUES (1, 1, 1)", "Only SELECT"),
        ("SELECT 1; DROP TABLE t_shirts", "exactly one"),
        ("PRAGMA query_only = OFF", "Only SELECT"),
        ("ATTACH DATABASE 'x.db' AS x", "Only SELECT"),
        ("SELECT * FROM users", "Unknown table"),
        ("SELECT * FROM main.t_shirts", "without a database prefix"),
        ("SELECT SLEEP(5)", "sleep"),
        ("SELECT load_extension('x')", "load_extension"),
        ("SELECT nonsense FROM", "Not valid"),
        ("x" * 4001, "longer than"),
    ],
)
def test_unsafe_or_invalid_sql_is_refused(sql, reason):
    with pytest.raises(UnsafeQueryError, match=reason):
        validate_sql(sql, "sqlite", TABLES)


def test_mysql_dialect_and_write_inside_cte_are_refused():
    with pytest.raises(UnsafeQueryError):
        validate_sql("SELECT * INTO OUTFILE '/tmp/x' FROM t_shirts", "mysql", TABLES)
    with pytest.raises(UnsafeQueryError):
        validate_sql("WITH x AS (DELETE FROM t_shirts RETURNING *) SELECT * FROM x", "sqlite", TABLES)
