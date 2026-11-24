"""AtliQ Tees source data: load and validate source/*.csv, then build the database.

The source of truth is two CSV files in source/:
- source/t_shirts.csv: t_shirt_id, brand, color, size, price, stock_quantity (80 rows)
- source/discounts.csv: discount_id, t_shirt_id, pct_discount (10 rows)

    python -m tshirt_agent.seed_data              # build data/atliq_tshirts.db (SQLite)
    python -m tshirt_agent.seed_data --mysql-sql  # write source/db_creation_atliq_t_shirts.sql
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tshirt_agent.config import DEFAULT_SQLITE_PATH, PROJECT_ROOT

SOURCE_DIR = PROJECT_ROOT / "source"
T_SHIRTS_CSV = "t_shirts.csv"
DISCOUNTS_CSV = "discounts.csv"
MYSQL_SCRIPT = SOURCE_DIR / "db_creation_atliq_t_shirts.sql"

BRANDS = ("Van Heusen", "Levi", "Nike", "Adidas")
COLORS = ("Red", "Blue", "Black", "White")
SIZES = ("XS", "S", "M", "L", "XL")
T_SHIRT_COLUMNS = ["t_shirt_id", "brand", "color", "size", "price", "stock_quantity"]
DISCOUNT_COLUMNS = ["discount_id", "t_shirt_id", "pct_discount"]

TShirt = tuple[int, str, str, str, int, int]  # (t_shirt_id, brand, color, size, price, stock_quantity)
Discount = tuple[int, int, float]  # (discount_id, t_shirt_id, pct_discount)


class DataError(ValueError):
    """A source CSV file is missing or has an invalid row."""


@dataclass(frozen=True)
class SourceData:
    t_shirts: list[TShirt]
    discounts: list[Discount]


def _read(path: Path, columns: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise DataError(f"Source file {path} not found.")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != columns:
            raise DataError(f"{path.name}: expected columns {columns}, got {reader.fieldnames}.")
        return list(reader)


def _int(row: dict[str, str], key: str, where: str) -> int:
    try:
        return int(row[key])
    except (TypeError, ValueError) as error:
        raise DataError(f"{where}: {key} must be a whole number, got {row[key]!r}.") from error


def load_t_shirts(path: Path) -> list[TShirt]:
    rows: list[TShirt] = []
    ids: set[int] = set()
    combos: set[tuple[str, str, str]] = set()
    for line, row in enumerate(_read(path, T_SHIRT_COLUMNS), start=2):
        where = f"{path.name} line {line}"
        t_id, price, stock = (_int(row, k, where) for k in ("t_shirt_id", "price", "stock_quantity"))
        brand, color, size = row["brand"], row["color"], row["size"]
        for value, allowed, name in ((brand, BRANDS, "brand"), (color, COLORS, "color"), (size, SIZES, "size")):
            if value not in allowed:
                raise DataError(f"{where}: {name} {value!r} is not one of {', '.join(allowed)}.")
        if not 10 <= price <= 50:
            raise DataError(f"{where}: price must be between 10 and 50, got {price}.")
        if stock < 0:
            raise DataError(f"{where}: stock_quantity cannot be negative.")
        if t_id in ids:
            raise DataError(f"{where}: duplicate t_shirt_id {t_id}.")
        if (brand, color, size) in combos:
            raise DataError(f"{where}: duplicate brand/color/size {brand}/{color}/{size}.")
        ids.add(t_id)
        combos.add((brand, color, size))
        rows.append((t_id, brand, color, size, price, stock))
    return rows


def load_discounts(path: Path, t_shirt_ids: set[int]) -> list[Discount]:
    rows: list[Discount] = []
    seen_ids: set[int] = set()
    discounted: set[int] = set()
    for line, row in enumerate(_read(path, DISCOUNT_COLUMNS), start=2):
        where = f"{path.name} line {line}"
        discount_id, t_id = _int(row, "discount_id", where), _int(row, "t_shirt_id", where)
        try:
            pct = float(row["pct_discount"])
        except ValueError as error:
            raise DataError(f"{where}: pct_discount must be a number, got {row['pct_discount']!r}.") from error
        if not 0 <= pct <= 100:
            raise DataError(f"{where}: pct_discount must be between 0 and 100, got {pct}.")
        if t_id not in t_shirt_ids:
            raise DataError(f"{where}: t_shirt_id {t_id} does not exist in {T_SHIRTS_CSV}.")
        if discount_id in seen_ids:
            raise DataError(f"{where}: duplicate discount_id {discount_id}.")
        if t_id in discounted:
            raise DataError(f"{where}: t_shirt_id {t_id} already has a discount.")
        seen_ids.add(discount_id)
        discounted.add(t_id)
        rows.append((discount_id, t_id, pct))
    return rows


def load_source(source_dir: Path = SOURCE_DIR) -> SourceData:
    """Read and validate both CSV files; raises DataError naming the file and line."""
    t_shirts = load_t_shirts(source_dir / T_SHIRTS_CSV)
    discounts = load_discounts(source_dir / DISCOUNTS_CSV, {row[0] for row in t_shirts})
    return SourceData(t_shirts, discounts)


SQLITE_SCHEMA = """
CREATE TABLE t_shirts (
    t_shirt_id INTEGER PRIMARY KEY,
    brand TEXT NOT NULL CHECK (brand IN ('Van Heusen', 'Levi', 'Nike', 'Adidas')),
    color TEXT NOT NULL CHECK (color IN ('Red', 'Blue', 'Black', 'White')),
    size TEXT NOT NULL CHECK (size IN ('XS', 'S', 'M', 'L', 'XL')),
    price INTEGER NOT NULL CHECK (price BETWEEN 10 AND 50),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    UNIQUE (brand, color, size)
);
CREATE TABLE discounts (
    discount_id INTEGER PRIMARY KEY,
    t_shirt_id INTEGER NOT NULL UNIQUE REFERENCES t_shirts(t_shirt_id),
    pct_discount REAL NOT NULL CHECK (pct_discount BETWEEN 0 AND 100)
);
"""


def build_sqlite(path: Path = DEFAULT_SQLITE_PATH, source_dir: Path = SOURCE_DIR) -> Path:
    """(Re)create the SQLite database from the source CSVs, atomically."""
    data = load_source(source_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.unlink(missing_ok=True)
    connection = sqlite3.connect(staging)
    try:
        with connection:
            connection.executescript(SQLITE_SCHEMA)
            connection.executemany("INSERT INTO t_shirts VALUES (?, ?, ?, ?, ?, ?)", data.t_shirts)
            connection.executemany("INSERT INTO discounts VALUES (?, ?, ?)", data.discounts)
    finally:
        connection.close()
    staging.replace(path)
    return path


def mysql_script(source_dir: Path = SOURCE_DIR) -> str:
    """The same source data as a MySQL script (CREATE DATABASE, tables, INSERTs)."""
    data = load_source(source_dir)
    t_shirts = ",\n".join(f"({i}, '{b}', '{c}', '{s}', {p}, {q})" for i, b, c, s, p, q in data.t_shirts)
    discounts = ",\n".join(f"({i}, {t}, {pct:.2f})" for i, t, pct in data.discounts)
    return f"""-- AtliQ Tees database for MySQL.
-- Generated from source/t_shirts.csv and source/discounts.csv by: make mysql-sql
CREATE DATABASE IF NOT EXISTS atliq_tshirts;
USE atliq_tshirts;

DROP TABLE IF EXISTS discounts;
DROP TABLE IF EXISTS t_shirts;

CREATE TABLE t_shirts (
    t_shirt_id INT PRIMARY KEY,
    brand ENUM('Van Heusen', 'Levi', 'Nike', 'Adidas') NOT NULL,
    color ENUM('Red', 'Blue', 'Black', 'White') NOT NULL,
    size ENUM('XS', 'S', 'M', 'L', 'XL') NOT NULL,
    price INT NOT NULL CHECK (price BETWEEN 10 AND 50),
    stock_quantity INT NOT NULL CHECK (stock_quantity >= 0),
    UNIQUE KEY brand_color_size (brand, color, size)
);

CREATE TABLE discounts (
    discount_id INT PRIMARY KEY,
    t_shirt_id INT NOT NULL UNIQUE,
    pct_discount DECIMAL(5,2) NOT NULL CHECK (pct_discount BETWEEN 0 AND 100),
    FOREIGN KEY (t_shirt_id) REFERENCES t_shirts(t_shirt_id)
);

INSERT INTO t_shirts (t_shirt_id, brand, color, size, price, stock_quantity) VALUES
{t_shirts};

INSERT INTO discounts (discount_id, t_shirt_id, pct_discount) VALUES
{discounts};

-- A read-only user for the app (change the password):
-- CREATE USER 'atliq_reader'@'localhost' IDENTIFIED BY 'change_me';
-- GRANT SELECT ON atliq_tshirts.* TO 'atliq_reader'@'localhost';
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AtliQ Tees database from source/*.csv.")
    parser.add_argument("--mysql-sql", action="store_true", help="write the MySQL script instead")
    args = parser.parse_args()
    try:
        if args.mysql_sql:
            MYSQL_SCRIPT.write_text(mysql_script(), encoding="utf-8")
            print(f"Wrote {MYSQL_SCRIPT.relative_to(PROJECT_ROOT)}")
        else:
            data = load_source()
            path = build_sqlite()
            print(
                f"Built {path.relative_to(PROJECT_ROOT)} from source/: "
                f"{len(data.t_shirts)} t-shirts, {len(data.discounts)} discounts"
            )
    except DataError as error:
        raise SystemExit(f"Source data error: {error}") from error


if __name__ == "__main__":
    main()
