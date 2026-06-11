import pytest
from scrapy.exceptions import DropItem

from olms.pipelines import Nullify, TitleCase


def employer_item(**overrides):
    item = {
        "termDate": None,
        "empTrdName": None,
        "empLabOrg": None,
        "state": None,
        "city": None,
        "amount": None,
    }
    item.update(overrides)
    return item


def test_nullify_drops_all_empty_rows_with_message():
    with pytest.raises(DropItem):
        Nullify().process_item(employer_item(city=""), spider=None)


def test_nullify_normalizes_synonyms_but_keeps_row():
    item = Nullify().process_item(
        employer_item(empLabOrg="ACME", termDate="Not Available"), spider=None
    )
    assert item["termDate"] is None
    assert item["empLabOrg"] == "ACME"


def test_titlecase():
    item = TitleCase().process_item({"city": "CHICAGO"}, spider=None)
    assert item["city"] == "Chicago"
