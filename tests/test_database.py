import pytest
from sqlalchemy import text

from tests.fakes import ROWS, inventory_value, stock
from tshirt_agent.agent.examples import SQL_EXAMPLES
from tshirt_agent.database import (
    DatabaseNotReadyError,
    QueryError,
    create_readonly_engine,
    describe_tables,
    run_query,
)
from tshirt_agent.seed_data import BRANDS, COLORS, SIZES, mysql_script


def test_source_data_is_complete_and_valid():
    assert len(ROWS) == len(BRANDS) * len(COLORS) * len(SIZES) == 80
    assert {(r[1], r[2], r[3]) for r in ROWS} == {(b, c, s) for b in BRANDS for c in COLORS for s in SIZES}
    assert all(10 <= r[4] <= 50 and r[5] >= 0 for r in ROWS)
    assert "Van Heusen" in BRANDS  # was misspelled "Van Huesen"


def test_mysql_script_has_the_same_data_as_source():
    script = mysql_script()
    assert "RAND()" not in script and script.count("'Van Heusen'") == 21  # 20 rows + ENUM
    first, last = ROWS[0], ROWS[-1]
    assert f"({first[0]}, '{first[1]}', '{first[2]}', '{first[3]}', {first[4]}, {first[5]})" in script
    assert f"({last[0]}, '{last[1]}', '{last[2]}', '{last[3]}', {last[4]}, {last[5]})" in script


def test_committed_mysql_script_is_up_to_date():
    from tshirt_agent.seed_data import MYSQL_SCRIPT

    assert MYSQL_SCRIPT.read_text(encoding="utf-8") == mysql_script(), "run: make mysql-sql"


def test_schema_lists_tables_values_and_keys(engine):
    tables = {t.name: t for t in describe_tables(engine)}
    assert {name: t.row_count for name, t in tables.items()} == {"discounts": 10, "t_shirts": 80}
    brand = next(c for c in tables["t_shirts"].columns if c["name"] == "brand")
    assert brand["values"] == ["Adidas", "Levi", "Nike", "Van Heusen"]
    assert tables["discounts"].foreign_keys == ["t_shirt_id → t_shirts(t_shirt_id)"]


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, stock("Nike", "White", "XS")),
        (1, inventory_value(size="S")),
        (2, inventory_value(brand="Levi", discounted=True)),
        (3, inventory_value(brand="Levi")),
    ],
)
def test_example_queries_return_the_python_ground_truth(engine, index, expected):
    result = run_query(engine, SQL_EXAMPLES[index][1], 50, 5)
    assert result.rows[0][0] == pytest.approx(expected)


def test_top_brand_example(engine):
    result = run_query(engine, SQL_EXAMPLES[4][1], 50, 5)
    expected = max(BRANDS, key=lambda b: stock(brand=b))
    assert result.rows == [[expected, stock(brand=expected)]]


def test_rows_are_capped_and_marked_truncated(engine):
    result = run_query(engine, "SELECT t_shirt_id FROM t_shirts", 5, 5)
    assert len(result.rows) == 5 and result.truncated


def test_database_errors_become_query_errors(engine):
    with pytest.raises(QueryError, match="no such column"):
        run_query(engine, "SELECT nope FROM t_shirts", 5, 5)


def test_slow_queries_are_stopped(engine):
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    with pytest.raises(QueryError, match="longer than 1s"):
        run_query(engine, slow, 5, 1)


def test_connection_is_read_only_even_without_the_guard(engine):
    with pytest.raises(Exception, match="readonly|read-only|read only"), engine.connect() as connection:
        connection.execute(text("UPDATE t_shirts SET price = 10"))
    assert run_query(engine, "SELECT SUM(price) FROM t_shirts", 5, 5).rows[0][0] == sum(r[4] for r in ROWS)


def test_missing_database_file_says_run_make_db(tmp_path):
    with pytest.raises(DatabaseNotReadyError, match="make db"):
        create_readonly_engine(f"sqlite:///{tmp_path / 'missing.db'}")
    assert not (tmp_path / "missing.db").exists()  # nothing was created
