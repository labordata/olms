import io
import sqlite3
import sys
from unittest.mock import patch

import pytest

from olms.merge import (
    delete_by_rptid,
    incoming_rptids,
    insert_rows,
    main,
    merge,
    tables_with_rptid,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE widget (rptId INTEGER, name TEXT, size INTEGER)"
    )
    conn.execute("CREATE TABLE filer (srNum INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO widget VALUES (1, 'old', 10), (2, 'keep', 20)")
    conn.execute("INSERT INTO filer VALUES (1, 'a')")
    conn.commit()
    conn.close()
    return path


def run_main(db, table, stdin, *flags, **kwargs):
    argv = ["merge_csv", str(db), table, *flags]
    with patch.object(sys, "argv", argv), patch.object(
        sys, "stdin", io.StringIO(stdin)
    ):
        main(**kwargs)


def test_default_merge_deletes_incoming_rptids_and_inserts(db):
    run_main(db, "widget", "rptId,name\n1,new\n")
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT rptId, name, size FROM widget ORDER BY rptId").fetchall()
    # rptId 1 replaced (size NULL — column absent from batch), rptId 2 untouched
    assert rows == [(1, "new", None), (2, "keep", 20)]


def test_empty_string_becomes_null(db):
    run_main(db, "widget", "rptId,name,size\n3,,\n")
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT name, size FROM widget WHERE rptId = 3"
    ).fetchone() == (None, None)


def test_replace_upserts_on_pk(db):
    run_main(db, "filer", "srNum,name\n1,updated\n2,new\n", "--replace")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM filer").fetchone() == (2,)
    assert conn.execute("SELECT name FROM filer WHERE srNum = 1").fetchone() == (
        "updated",
    )


def test_unknown_column_is_an_error(db):
    with pytest.raises(SystemExit, match="bogus"):
        run_main(db, "widget", "rptId,bogus\n1,1\n")


def test_ignore_drops_column(db):
    run_main(db, "widget", "rptId,name,bogus\n5,x,1\n", "--ignore", "bogus")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM widget WHERE rptId = 5").fetchone() == ("x",)


def test_internal_columns_are_allowed(db):
    def strategy(conn, table, columns, rows):
        keep = [c for c in columns if c != "ordinal"]
        return 0, insert_rows(conn, table, keep, rows)

    run_main(
        db,
        "widget",
        "rptId,name,ordinal\n6,x,0\n",
        strategies={"widget": strategy},
        internal_columns={"widget": {"ordinal"}},
    )
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM widget WHERE rptId = 6").fetchone() == ("x",)


def test_duplicate_headers_are_an_error(db):
    with pytest.raises(SystemExit, match="duplicate"):
        run_main(db, "widget", "rptId,name,name\n1,a,b\n")


def test_empty_stdin_is_a_noop(db, capsys):
    run_main(db, "widget", "")
    assert "no rows to merge" in capsys.readouterr().err
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM widget").fetchone() == (2,)


def test_empty_row_check_skips(db, capsys):
    run_main(
        db,
        "widget",
        "rptId,name,size\n7,,\n8,real,1\n",
        empty_row_check={"widget": ("name", "size")},
    )
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT count(*) FROM widget WHERE rptId IN (7, 8)"
    ).fetchone() == (1,)
    assert "skipped 1 empty rows" in capsys.readouterr().err


def test_strategy_dispatch(db):
    seen = {}

    def strategy(conn, table, columns, rows):
        seen["table"] = table
        seen["rows"] = rows
        return 0, 0

    run_main(db, "widget", "rptId,name\n9,x\n", strategies={"widget": strategy})
    assert seen["table"] == "widget"
    assert seen["rows"] == [{"rptId": "9", "name": "x"}]


def test_primitives(db):
    conn = sqlite3.connect(db)
    assert tables_with_rptid(conn) == ["widget"]
    assert incoming_rptids([{"rptId": "5"}, {"rptId": "1"}]) == {1, 5}
    assert delete_by_rptid(conn, "widget", {1}) == 1
    assert delete_by_rptid(conn, "widget", set()) == 0
    deleted, inserted = merge(
        conn, "widget", ["rptId", "name"], [{"rptId": 2, "name": "z"}]
    )
    assert (deleted, inserted) == (1, 1)


def test_prune_missing_deletes_gone_rows_with_guard(db):
    import sqlite3 as s

    from olms.merge import prune_missing

    conn = s.connect(db)
    conn.execute("CREATE TABLE filing (rptId INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO filing VALUES (?)", [(i,) for i in range(10)])

    # widget rows for rptIds 1 and 2 exist from the fixture
    reports = prune_missing(conn, set(range(1, 10)))  # 0 is gone upstream
    assert any("1 filing rows" in r for r in reports)
    assert conn.execute("SELECT count(*) FROM filing").fetchone() == (9,)

    with pytest.raises(SystemExit, match="partial crawl"):
        prune_missing(conn, {1, 2})  # would delete 7 of 9
