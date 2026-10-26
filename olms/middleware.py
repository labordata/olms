"""OLMS rate-limits aggressive clients with HTTP 403s.

Treat those as "slow down", not as content: back off the download slot,
retry the request a few times, and if the server keeps blocking anyway,
close the spider so the run ends with an obviously short output (which
the consuming pipeline's output guards turn into a failure) instead of
quietly merging partial data.

403 should NOT also be in RETRY_HTTP_CODES: the stock RetryMiddleware
retries immediately with no backoff, which just re-spends the request
against the rate limiter.
"""

import logging

from scrapy.downloadermiddlewares.retry import get_retry_request

logger = logging.getLogger(__name__)


class StickyBadNodeRetryMiddleware:
    """olmsapps.dol.gov sits behind a load balancer whose backend nodes
    don't all have every olpdr servlet's virtual host deployed. Which
    node a connection lands on is decided once, when the TCP connection
    is opened — not per request — and stays fixed for that connection's
    whole lifetime: confirmed by reusing one connection for 8+ requests
    after it first 404'd, and it 404'd every single time. A fresh
    connection gets an independent new chance at a working node (also
    confirmed: a known-good srNum flipped 200/404 across separate
    connections with no pattern).

    So, unlike a real block (BlockingBackoffMiddleware), this isn't
    about slowing down — slowing down doesn't change which node you
    land on — and backing off the whole domain over it (what we tried
    first) stalls every other in-flight request behind one unlucky
    connection for no reason. The fix is just to get off the bad
    connection: Twisted's HTTP connection pool otherwise happily reuses
    it (and keeps 404ing) since the error response itself doesn't say
    Connection: close. A handful of quick retries, each discarding the
    bad connection via our own Connection: close, is usually enough —
    at roughly a coin-flip chance of landing on a working node, 4
    retries gets well above 90% odds of recovering.

    A servlet where every backend node is broken (lm10's detail
    servlet, as far as we've seen) will still exhaust these retries and
    fail — no client-side fix can route around that — but at least it
    fails fast with the real cause in the log, instead of being read as
    a sustained block.
    """

    SIGNATURE = b"has not been defined"
    MAX_RETRIES = 4

    def __init__(self, crawler):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_response(self, request, response, spider=None):
        if spider is None:
            spider = self.crawler.spider

        if response.status != 404 or self.SIGNATURE not in response.body:
            return response

        retry = get_retry_request(
            request,
            spider=spider,
            reason="bad backend node (SRVE0255E)",
            max_retry_times=self.MAX_RETRIES,
            stats_base_key="sticky_bad_node",
        )
        if retry is None:
            logger.error(
                "%s still 404ing a bad backend node after %d retries; "
                "giving up on %s",
                request.url,
                self.MAX_RETRIES,
                request.body,
            )
            return response

        logger.warning(
            "HTTP 404 from %s (body=%r) looks like a bad backend node, "
            "not a real not-found; retrying on a fresh connection",
            request.url,
            request.body,
        )
        retry.headers["Connection"] = "close"
        return retry


class BlockingBackoffMiddleware:
    BLOCK_CODES = (403, 429)
    MAX_RETRIES = 4
    MAX_CONSECUTIVE_BLOCKS = 50
    INITIAL_BACKOFF = 5.0
    MAX_BACKOFF = 300.0

    def __init__(self, crawler):
        self.crawler = crawler
        self.consecutive_blocks = 0
        self.closing = False

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_response(self, request, response, spider=None):
        # scrapy >= 2.16 deprecates the spider argument
        if spider is None:
            spider = self.crawler.spider

        if response.status not in self.BLOCK_CODES:
            self.consecutive_blocks = 0
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
            logger.warning(
                "HTTP %s from %s; backing off download slot to %.0fs",
                response.status,
                request.url,
                new_delay,
            )
            slot.delay = new_delay
