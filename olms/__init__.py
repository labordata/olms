"""Shared scraping infrastructure for labordata's DOL OLMS pipelines.

DOL's OLMS endpoints (olmsapps.dol.gov) rate-limit aggressive clients
with 403s; the pieces here encode the crawling posture the lm10 and
lm20 pipelines converged on: identify consistently, throttle by
latency, back off explicitly on blocks, and share one HTTP cache
across the spiders of a run.
"""

__version__ = "0.1.0"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
