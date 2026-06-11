import pytest

from olms.flatten import COLUMN, IGNORE, FlattenError, Table, flatten

FORM = {
    "100": {
        "formFiled": "LM-20",
        "person_filing": {
            "name_and_mailing_address": {"name": "A", "city": "Chicago"},
            "other": {"name": "B", "city": ""},
        },
        "signatures": {"13": {"signed": "X"}, "14": {"signed": "Y"}},
        "specific_activities": [
            {
                "nature_of_activity": "persuade",
                "performers": [{"name": "P1"}, {"name": "P2"}],
            }
        ],
        "employer": {"name": "ACME", "ein": "1"},
        "junk": {"drop": "me"},
    }
}

SPEC = [
    ("", Table("form", keys=("rptId",), emit=False)),
    ("*/person_filing", Table("contact", keys=("rptId", "contact_type"))),
    ("*/signatures", Table("signatures", keys=("rptId", "signature_number"))),
    (
        "*/specific_activities",
        Table(
            "specific_activity",
            keys=("rptId", "activity_order"),
            renames={"nature_of_activity": "specific_nature_of_activity"},
        ),
    ),
    (
        "*/specific_activities/*/performers",
        Table(
            "performer",
            keys=("rptId", "specific_activity_id", "performer_order"),
        ),
    ),
    ("*/employer", COLUMN),
    ("*/junk", IGNORE),
]


def test_tables_keys_and_renames():
    tables = flatten(FORM, SPEC)

    assert "form" not in tables  # emit=False

    assert tables["contact"] == [
        {"rptId": "100", "contact_type": "name_and_mailing_address", "name": "A", "city": "Chicago"},
        {"rptId": "100", "contact_type": "other", "name": "B", "city": ""},
    ]

    assert tables["signatures"] == [
        {"rptId": "100", "signature_number": "13", "signed": "X"},
        {"rptId": "100", "signature_number": "14", "signed": "Y"},
    ]

    # list children get 0-based stringified ordinals; per-table key
    # naming gives the SAME ancestor key different names per table
    assert tables["specific_activity"] == [
        {
            "rptId": "100",
            "activity_order": "0",
            "specific_nature_of_activity": "persuade",
        }
    ]
    assert tables["performer"] == [
        {"rptId": "100", "specific_activity_id": "0", "performer_order": "0", "name": "P1"},
        {"rptId": "100", "specific_activity_id": "0", "performer_order": "1", "name": "P2"},
    ]


def test_column_flattens_into_parent_row_with_leaf_names():
    spec = [
        ("", Table("lm20", keys=("rptId",))),
        ("*/employer", COLUMN),
    ]
    tables = flatten({"5": {"amended": "N", "employer": {"name": "ACME", "ein": "1"}}}, spec)
    assert tables["lm20"] == [{"rptId": "5", "amended": "N", "name": "ACME", "ein": "1"}]


def test_unhandled_dict_under_column_flattens_by_leaf():
    spec = [
        ("", Table("t", keys=("rptId",))),
        ("*/outer", COLUMN),
    ]
    tables = flatten({"1": {"outer": {"inner": {"leaf": "v"}}}}, spec)
    assert tables["t"] == [{"rptId": "1", "leaf": "v"}]


def test_drop_and_collision():
    spec = [("", Table("t", keys=("rptId",), drop=frozenset({"noise"})))]
    tables = flatten({"1": {"noise": "x", "keep": "y"}}, spec)
    assert tables["t"] == [{"rptId": "1", "keep": "y"}]

    collide = [
        ("", Table("t", keys=("rptId",))),
        ("*/a", COLUMN),
        ("*/b", COLUMN),
    ]
    with pytest.raises(FlattenError, match="assigned twice"):
        flatten({"1": {"a": {"x": 1}, "b": {"x": 2}}}, collide)


def test_single_element_list_column():
    spec = [
        ("", Table("t", keys=("rptId",))),
        ("*/sched", COLUMN),
    ]
    tables = flatten({"1": {"sched": [{"Amount": "5"}]}}, spec)
    assert tables["t"] == [{"rptId": "1", "Amount": "5"}]

    assert flatten({"1": {"sched": []}}, spec)["t"] == [{"rptId": "1"}]

    with pytest.raises(FlattenError, match="cannot hold more than one"):
        flatten({"1": {"sched": [{"Amount": "5"}, {"Amount": "6"}]}}, spec)


def test_root_array_with_suppressed_key():
    spec = [("", Table("filing", keys=(None,), drop=frozenset({"formLink"})))]
    tables = flatten([{"rptId": 9, "formLink": "x"}, {"rptId": 10}], spec)
    assert tables["filing"] == [{"rptId": 9}, {"rptId": 10}]


def test_unhandled_container_errors():
    with pytest.raises(FlattenError, match="no handler"):
        flatten({"1": {"mystery": {"a": 1}}}, [("", Table("t"))])
