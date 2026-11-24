import shutil

import pytest

from tshirt_agent.seed_data import SOURCE_DIR, DataError, build_sqlite, load_source

T_HEADER = "t_shirt_id,brand,color,size,price,stock_quantity\n"
D_HEADER = "discount_id,t_shirt_id,pct_discount\n"


@pytest.fixture
def source(tmp_path):
    """A writable copy of source/ to break on purpose."""
    folder = tmp_path / "source"
    shutil.copytree(SOURCE_DIR, folder)
    return folder


def write(folder, t_shirts=None, discounts=None):
    if t_shirts is not None:
        (folder / "t_shirts.csv").write_text(T_HEADER + t_shirts)
    if discounts is not None:
        (folder / "discounts.csv").write_text(D_HEADER + discounts)


def test_the_committed_source_files_load():
    data = load_source()
    assert (len(data.t_shirts), len(data.discounts)) == (80, 10)
    assert [d[1] for d in data.discounts] == list(range(1, 11))


@pytest.mark.parametrize(
    ("t_shirts", "discounts", "message"),
    [
        ("1,Puma,Red,XS,20,5\n", "", "brand 'Puma' is not one of"),
        ("1,Nike,Green,XS,20,5\n", "", "color 'Green'"),
        ("1,Nike,Red,XXL,20,5\n", "", "size 'XXL'"),
        ("1,Nike,Red,XS,99,5\n", "", "price must be between 10 and 50"),
        ("1,Nike,Red,XS,20,-1\n", "", "cannot be negative"),
        ("1,Nike,Red,XS,twenty,5\n", "", "price must be a whole number"),
        ("1,Nike,Red,XS,20,5\n1,Nike,Red,S,20,5\n", "", "duplicate t_shirt_id 1"),
        ("1,Nike,Red,XS,20,5\n2,Nike,Red,XS,30,5\n", "", "duplicate brand/color/size"),
        ("1,Nike,Red,XS,20,5\n", "1,9,10\n", "t_shirt_id 9 does not exist"),
        ("1,Nike,Red,XS,20,5\n", "1,1,150\n", "between 0 and 100"),
        ("1,Nike,Red,XS,20,5\n", "1,1,10\n2,1,20\n", "already has a discount"),
        ("1,Nike,Red,XS,20,5\n", "1,1,ten\n", "must be a number"),
    ],
)
def test_invalid_rows_are_rejected_with_file_and_line(source, t_shirts, discounts, message):
    write(source, t_shirts, discounts)
    with pytest.raises(DataError, match=message) as error:
        load_source(source)
    assert "line " in str(error.value)


def test_wrong_header_and_missing_file(source):
    (source / "t_shirts.csv").write_text("id,brand\n1,Nike\n")
    with pytest.raises(DataError, match="expected columns"):
        load_source(source)
    (source / "t_shirts.csv").unlink()
    with pytest.raises(DataError, match="not found"):
        load_source(source)


def test_a_failed_build_leaves_the_old_database_untouched(source, tmp_path):
    db = build_sqlite(tmp_path / "shop.db", source)
    before = db.read_bytes()
    write(source, "1,Puma,Red,XS,20,5\n", "")
    with pytest.raises(DataError):
        build_sqlite(db, source)
    assert db.read_bytes() == before
