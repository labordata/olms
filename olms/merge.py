"""Name-based CSV → sqlite merge engine.

Reads a CSV of freshly scraped rows from stdin and merges it into one
table of the pipeline's database. CSV columns are matched to table
columns BY NAME, so the batch-dependent column subsets and orderings
that json-to-multicsv emits are safe: columns missing from a batch are
stored as NULL, empty strings become NULL (matching the
csvs-to-sqlite behavior of the full builds), and unknown columns are
an error unless --ignore'd or declared internal.

The default merge deletes the incoming batch's rptIds and re-inserts;
--replace upserts on the primary key instead. Anything richer — the
domain semantics of a particular table — is supplied by the consuming
pipeline as a strategy:

    def merge_filing(conn, table, columns, rows):
        ...
        return deleted_count, inserted_count

    main(strategies={"filing": merge_filing}, description=__doc__)

`internal_columns` declares CSV columns a strategy consumes that are
not table columns (e.g. ordinal keys it resolves); `empty_row_check`
maps tables to the column tuple whose joint emptiness drops a row.
"""

import argparse
import csv
import sqlite3
import sys


def quoted(column):
    return '"' + column.replace('"', '""') + '"'


def table_columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({quoted(table)})")]


def tables_with_rptid(conn):
    names = [
        name
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    return [name for name in names if "rptId" in table_columns(conn, name)]


def delete_by_rptid(conn, table, rpt_ids):
    if not rpt_ids:
        return 0
    placeholders = ", ".join("?" * len(rpt_ids))
    cursor = conn.execute(
        f"DELETE FROM {quoted(table)} WHERE rptId IN ({placeholders})",
        sorted(rpt_ids),
    )
    return cursor.rowcount


def incoming_rptids(rows):
    return {int(row["rptId"]) for row in rows}


def insert_rows(conn, table, columns, rows, replace=False):
    verb = "REPLACE" if replace else "INSERT"
    column_list = ", ".join(quoted(column) for column in columns)
    placeholders = ", ".join("?" * len(columns))
    conn.executemany(
        f"{verb} INTO {quoted(table)} ({column_list}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )
    return len(rows)


def merge(conn, table, columns, rows, replace=False, strategy=None):
    if strategy is not None:
        return strategy(conn, table, columns, rows)
    if replace:
        return 0, insert_rows(conn, table, columns, rows, replace=True)
    deleted = delete_by_rptid(conn, table, incoming_rptids(rows))
    return deleted, insert_rows(conn, table, columns, rows)


def main(strategies=None, empty_row_check=None, internal_columns=None, description=None):
    strategies = strategies or {}
    empty_row_check = empty_row_check or {}
    internal_columns = internal_columns or {}

    parser = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db")
    parser.add_argument("table")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="upsert on the primary key instead of deleting by rptId",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="COLUMN",
        help="CSV column to drop (a field the full build's sqlite-utils"
        " transform drops)",
    )
    args = parser.parse_args()

    reader = csv.DictReader(sys.stdin)
    raw_rows = list(reader)
    if not raw_rows or not reader.fieldnames:
        print(f"{args.table}: no rows to merge", file=sys.stderr)
        return

    header = [column for column in reader.fieldnames if column not in args.ignore]
    if len(set(header)) != len(header):
        raise SystemExit(f"{args.table}: duplicate CSV columns: {header}")

    conn = sqlite3.connect(args.db)
    columns = table_columns(conn, args.table)
    if not columns:
        raise SystemExit(f"{args.db} has no table {args.table}")
    internal = set(internal_columns.get(args.table, ()))
    unknown = sorted(set(header) - set(columns) - internal)
    if unknown:
        raise SystemExit(
            f"{args.table}: CSV columns {unknown} are not in the table;"
            " if the full build drops them, pass --ignore"
        )

    rows = [{column: row[column] or None for column in header} for row in raw_rows]

    if args.table in empty_row_check:
        check = empty_row_check[args.table]
        kept = [row for row in rows if any(row.get(column) for column in check)]
        if len(kept) < len(rows):
            print(
                f"{args.table}: skipped {len(rows) - len(kept)} empty rows",
                file=sys.stderr,
            )
        rows = kept

    with conn:
        deleted, inserted = merge(
            conn,
            args.table,
            header,
            rows,
            replace=args.replace,
            strategy=strategies.get(args.table),
        )
    conn.close()

    print(f"{args.table}: -{deleted} +{inserted} rows", file=sys.stderr)
