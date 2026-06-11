from unittest.mock import Mock

from scrapy import Spider
from scrapy.http import Request, Response
from scrapy.settings import Settings

from olms.middleware import BlockingBackoffMiddleware


def make_spider():
    spider = Spider(name="t")
    spider.crawler = Mock(settings=Settings())
    return spider


def make_middleware(slot_delay=0.5):
    slot = Mock(delay=slot_delay)
    crawler = Mock()
    crawler.engine.downloader.slots.get.return_value = slot
    return BlockingBackoffMiddleware(crawler), crawler, slot


def blocked_response(request, status=403):
    return Response(request.url, status=status, request=request)


def test_403_backs_off_and_retries():
    mw, _, slot = make_middleware()
    spider = make_spider()
    request = Request("https://olmsapps.dol.gov/x", meta={"download_slot": "k"})

    retry = mw.process_response(request, blocked_response(request), spider)

    assert isinstance(retry, Request)
    assert retry.meta["retry_times"] == 1
    assert slot.delay == 5.0

    retry2 = mw.process_response(retry, blocked_response(retry), spider)
    assert retry2.meta["retry_times"] == 2
    assert slot.delay == 10.0


def test_backoff_is_capped():
    mw, _, slot = make_middleware(slot_delay=400)
    request = Request("https://olmsapps.dol.gov/x", meta={"download_slot": "k"})

    mw.process_response(request, blocked_response(request), make_spider())

    assert slot.delay == 400  # never raised above MAX_BACKOFF


def test_retry_exhaustion_passes_response_through():
    mw, _, _ = make_middleware()
    request = Request(
        "https://olmsapps.dol.gov/x",
        meta={"download_slot": "k", "retry_times": mw.MAX_RETRIES},
    )

    out = mw.process_response(request, blocked_response(request), make_spider())

    assert isinstance(out, Response)


def test_success_resets_consecutive_counter():
    mw, _, _ = make_middleware()
    spider = make_spider()
    request = Request("https://olmsapps.dol.gov/x", meta={"download_slot": "k"})

    mw.process_response(request, blocked_response(request), spider)
    assert mw.consecutive_blocks == 1

    mw.process_response(request, Response(request.url, status=200, request=request), spider)
    assert mw.consecutive_blocks == 0


def test_persistent_blocking_closes_spider_once():
    mw, crawler, _ = make_middleware()
    spider = make_spider()
    request = Request("https://olmsapps.dol.gov/x", meta={"download_slot": "k"})

    for _ in range(mw.MAX_CONSECUTIVE_BLOCKS + 10):
        mw.process_response(request, blocked_response(request), spider)

    crawler.engine.close_spider.assert_called_once_with(spider, "blocked_by_server")


def test_spider_argument_optional():
    # scrapy >= 2.16 stops passing the spider argument
    mw, crawler, _ = make_middleware()
    crawler.spider = make_spider()
    request = Request("https://olmsapps.dol.gov/x", meta={"download_slot": "k"})

    retry = mw.process_response(request, blocked_response(request))

    assert isinstance(retry, Request)


def test_429_also_treated_as_block():
    mw, _, slot = make_middleware()
    request = Request("https://olmsapps.dol.gov/x", meta={"download_slot": "k"})

    retry = mw.process_response(request, blocked_response(request, status=429), make_spider())

    assert isinstance(retry, Request)
    assert slot.delay == 5.0
