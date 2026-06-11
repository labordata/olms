"""Flatten nested JSON into per-table row dicts.

Replaces the json-to-multicsv + sed-rename layer of the OLMS
pipelines: same walk semantics (path handlers declaring which subtrees
are child tables, flattened columns, or ignored), but rows are
produced in memory with their FINAL column names — no CSV hop, no
header rewriting — and handed straight to olms.merge.

A spec is a list of handlers:

    SPEC = [
        ("", Table("form", keys=("rptId",), emit=False)),
        ("*/signatures", Table("signatures", keys=("rptId", "signature_number"))),
        ("*/specific_activities/*/performers",
         Table("performer", keys=("rptId", "specific_activity_id", "performer_order"))),
        ("*/direct_or_indirect", COLUMN),
        ("*/employer", IGNORE),
    ]

Handler paths are '/'-joined component patterns ('*' matches any one
component) rooted at the JSON document. A Table handler starts rows
for each child of the matched container; `keys` names the key columns
contributed by every Table level on the way down — one name per
ancestor level plus this one, in order, None to drop that key. COLUMN
flattens a dict's leaves into the current row (lists are allowed only
with a single element); IGNORE skips the subtree.

`renames` maps leaf field names to final column names; `drop` omits
leaf fields entirely.
"""

from dataclasses import dataclass, field


@dataclass
class Table:
    name: str
    keys: tuple = ()
    renames: dict = field(default_factory=dict)
    drop: frozenset = frozenset()
    emit: bool = True
    # ordinal for list children: the perl json-to-multicsv this engine
    # replaces counted from 1, the python port from 0 — and the
    # published ordinal columns (activity_order etc.) fossilized each
    # pipeline's base
    list_base: int = 0


COLUMN = "column"
IGNORE = "ignore"


class FlattenError(Exception):
    pass


def _parse_spec(spec):
    handlers = []
    for path, handler in spec:
        components = tuple(path.split("/")) if path else ()
        handlers.append((components, handler))
    return handlers


def _match(components, path):
    if len(components) != len(path):
        return False
    return all(c == "*" or c == p for c, p in zip(components, path))


def flatten(data, spec):
    """Walk `data` per `spec`; return {table_name: [row dicts]}."""
    handlers = _parse_spec(spec)
    tables = {h.name: [] for _, h in handlers if isinstance(h, Table) and h.emit}
    _walk(
        data,
        path=(),
        handlers=handlers,
        tables=tables,
        key_values=(),
        table=None,
        fieldname=None,
        row=None,
        in_column=False,
    )
    return tables


def _find(handlers, path):
    for components, handler in handlers:
        if _match(components, path):
            return handler
    return None


def _set(row, table, fieldname, value):
    fieldname = table.renames.get(fieldname, fieldname)
    if fieldname in table.drop:
        return
    if row is not None and fieldname in row:
        raise FlattenError(
            f"{table.name}: column {fieldname!r} assigned twice for one row"
        )
    row[fieldname] = value


def _walk(value, *, path, handlers, tables, key_values, table, fieldname, row, in_column):
    handler = _find(handlers, path)

    if handler is IGNORE:
        return

    if not isinstance(value, (dict, list)):
        if table is None:
            raise FlattenError(f"scalar at /{'/'.join(path)} outside any table")
        _set(row, table, fieldname, value)
        return

    if handler is None:
        if isinstance(value, dict) and in_column:
            # un-handled nested dict while flattening a declared column:
            # keep flattening by leaf name (matches the old pipeline's
            # strip-all-prefixes renaming)
            handler = COLUMN
        else:
            kind = "object" if isinstance(value, dict) else "array"
            raise FlattenError(
                f"no handler for the {kind} at /{'/'.join(path)};"
                " add a Table, COLUMN, or IGNORE entry"
            )

    if isinstance(handler, Table):
        children = (
            value.items()
            if isinstance(value, dict)
            else ((str(i), v) for i, v in enumerate(value, handler.list_base))
        )
        for child_key, child in children:
            child_keys = key_values + (child_key,)
            child_row = {}
            for name, key_value in zip(handler.keys, child_keys):
                if name is not None:
                    _set(child_row, handler, name, key_value)
            if handler.emit:
                tables[handler.name].append(child_row)
            if not isinstance(child, (dict, list)):
                _set(child_row, handler, handler.name, child)
                continue
            # the child container is this row's body: its entries are
            # the row's fields (and any nested table/column subtrees)
            fields = (
                child.items()
                if isinstance(child, dict)
                else ((str(i), v) for i, v in enumerate(child))
            )
            for field_key, field_value in fields:
                _walk(
                    field_value,
                    path=path + (child_key, field_key),
                    handlers=handlers,
                    tables=tables,
                    key_values=child_keys,
                    table=handler,
                    fieldname=field_key,
                    row=child_row,
                    in_column=False,
                )
        return

    # COLUMN: flatten into the current row using leaf names
    if isinstance(value, list):
        if len(value) > 1:
            raise FlattenError(
                f"{table.name}: list at /{'/'.join(path)} has {len(value)}"
                " elements but the table stores single-element columns;"
                " the schema cannot hold more than one"
            )
        for element in value[:1]:
            _walk(
                element,
                path=path + ("0",),
                handlers=handlers,
                tables=tables,
                key_values=key_values,
                table=table,
                fieldname=fieldname,
                row=row,
                in_column=True,
            )
        return

    for child_key, child in value.items():
        _walk(
            child,
            path=path + (child_key,),
            handlers=handlers,
            tables=tables,
            key_values=key_values,
            table=table,
            fieldname=child_key,
            row=row,
            in_column=True,
        )
