import asyncio

import pytest
from scrapy import Spider

from olms.http import form_request
from olms.spiders import SrNumSpiderMixin


class FakeSpider(SrNumSpiderMixin, Spider):
    name = "fake"

    def _detail_request(self, sr_num):
        return form_request(
            "https://olmsapps.dol.gov/olpdr/Detail",
            formdata={"srNum": "C-" + str(sr_num)},
        )


def collect_start(spider):
    async def collect():
        return [r async for r in spider.start()]

    return asyncio.run(collect())


def test_sr_nums_parsed_deduped_sorted():
    spider = FakeSpider(sr_nums="556,42,42")
    assert spider.sr_nums == [42, 556]


def test_sr_nums_file(tmp_path):
    path = tmp_path / "sr_nums.txt"
    path.write_text("7\n3\n7\n")
    spider = FakeSpider(sr_nums_file=str(path))
    assert spider.sr_nums == [3, 7]


def test_start_yields_detail_requests():
    spider = FakeSpider(sr_nums="42,556")
    requests = collect_start(spider)
    assert [r.body for r in requests] == [b"srNum=C-42", b"srNum=C-556"]
    assert all(r.method == "POST" for r in requests)


def test_no_input_raises():
    with pytest.raises(ValueError):
        FakeSpider()


def test_empty_file_raises(tmp_path):
    path = tmp_path / "sr_nums.txt"
    path.write_text("")
    with pytest.raises(ValueError):
        FakeSpider(sr_nums_file=str(path))
