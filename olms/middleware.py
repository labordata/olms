"""OLMS rate-limits aggressive clients with HTTP 403s.

Treat those as "slow down", not as content: back off the download slot,
retry the request a few times, and if the server keeps blocking anyway,
close the spider so the run ends with an obviously short output (which
the consuming pipeline's output guards turn into a failure) instead of
quietly merging partial data.

403 should NOT also be in RETRY_HTTP_CODES: the stock RetryMiddleware
retries immediately with no backoff, which just re-spends the request
against the rate limiter.

Some servlets also disguise blocking as a 404 (a genuine not-found from
them is a 200 with an empty body/result) rather than the usual 403/429
— unlike other servlets, where a 404 is a real, consistently-reproducible
routing error that backing off won't fix. BLOCK_CODES is therefore
configurable per spider via the BLOCKING_BACKOFF_CODES setting rather
than hardcoded for every spider using this middleware.

A spider that opts into treating 404 as blocking still makes one
ROBOTSTXT_OBEY request per domain, and OLMS genuinely has no
robots.txt there — that 404 is normal (scrapy's own robots.txt
middleware already treats a missing robots.txt as "no restrictions")
and is excluded here so it isn't mistaken for a block.

The backoff delay only ever climbs on a block; it also eases back down
on success, so a sustained run of blocks still slows the crawl a lot,
but scattered one-off blocks amid an otherwise-successful crawl don't
permanently ratchet the delay toward MAX_BACKOFF.
"""

import logging

from scrapy.downloadermiddlewares.retry import get_retry_request

logger = logging.getLogger(__name__)


class BlockingBackoffMiddleware:
    BLOCK_CODES = (403, 429)
    MAX_RETRIES = 4
    MAX_CONSECUTIVE_BLOCKS = 50
    INITIAL_BACKOFF = 5.0
    MAX_BACKOFF = 300.0

    def __init__(self, crawler, block_codes=None):
        self.crawler = crawler
        self.block_codes = tuple(block_codes) if block_codes else self.BLOCK_CODES
        self.consecutive_blocks = 0
        self.closing = False

    @classmethod
    def from_crawler(cls, crawler):
        block_codes = crawler.settings.getlist(
            "BLOCKING_BACKOFF_CODES", cls.BLOCK_CODES
        )
        return cls(crawler, block_codes=block_codes)

    def process_response(self, request, response, spider=None):
        # scrapy >= 2.16 deprecates the spider argument
        if spider is None:
            spider = self.crawler.spider

        if response.status not in self.block_codes or request.url.endswith(
            "/robots.txt"
        ):
            self.consecutive_blocks = 0
            self._ease_off(request)
            return response

        self.consecutive_blocks += 1
        if self.consecutive_blocks >= self.MAX_CONSECUTIVE_BLOCKS:
            if not self.closing:
                self.closing = True
                logger.error(
                    "%d consecutive %s responses; OLMS has blocked us — "
                    "closing %s",
                    self.consecutive_blocks,
                    response.status,
                    spider.name,
                )
                self.crawler.engine.close_spider(spider, "blocked_by_server")
            return response

        self._back_off(request, response)
        retry = get_retry_request(
            request,
            spider=spider,
            reason=f"HTTP {response.status} (rate limited)",
            max_retry_times=self.MAX_RETRIES,
            stats_base_key="block_backoff",
        )
        if retry is None:
            return response
        return retry

    def _back_off(self, request, response):
        key = request.meta.get("download_slot")
        slot = self.crawler.engine.downloader.slots.get(key) if key else None
        if slot is None:
            return
        new_delay = min(max(slot.delay * 2, self.INITIAL_BACKOFF), self.MAX_BACKOFF)
        if new_delay > slot.delay:
            body = request.body.decode("utf-8", errors="replace") if request.body else ""
            logger.warning(
                "HTTP %s from %s (retry %d, body=%r); backing off download slot to %.0fs",
                response.status,
                request.url,
                request.meta.get("retry_times", 0),
                body,
                new_delay,
            )
            slot.delay = new_delay

    def _ease_off(self, request):
        # Without this, a scattered one-off block amid an otherwise
        # successful crawl permanently ratchets the slot delay toward
        # MAX_BACKOFF, since _back_off only ever raises it. Halve it
        # back down on success instead, so only a sustained run of
        # blocks (not occasional, isolated ones) keeps the delay high.
        key = request.meta.get("download_slot")
        slot = self.crawler.engine.downloader.slots.get(key) if key else None
        if slot is None or slot.delay <= self.INITIAL_BACKOFF:
            return
        slot.delay = max(slot.delay / 2, self.INITIAL_BACKOFF)
