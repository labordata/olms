# olms

Shared scraping infrastructure for [labordata](https://github.com/labordata)'s
DOL OLMS pipelines ([lm10](https://github.com/labordata/lm10),
[lm20](https://github.com/labordata/lm20)).

OLMS (olmsapps.dol.gov) rate-limits aggressive clients with 403s. This package
holds the crawling posture both pipelines converged on, extracted after the
two copies were brought back into sync (labordata/lm20#24 and the lm10 port):

- `olms.middleware.BlockingBackoffMiddleware` — treat 403/429 as
  rate-limiting, not content: exponential download-slot backoff plus retries,
  and close the spider after 50 consecutive blocks so a blocked crawl fails
  loudly instead of merging partial data. Keep 403 out of RETRY_HTTP_CODES.
- `olms.cache.SharedFilesystemCacheStorage` — de-namespace scrapy's HTTP cache
  so sibling spiders in one run share detail-servlet responses. Pair with
  `HTTPCACHE_IGNORE_HTTP_CODES` so a cached 403 can't satisfy its own retry.
- `olms.spiders.SrNumSpiderMixin` — restrict a full-crawl spider to an
  explicit filer list (`-a sr_nums=…` / `-a sr_nums_file=…`); the host spider
  supplies `_detail_request(sr_num)`.
- `olms.http.form_request` — form-encoded POST helper (scrapy's FormRequest
  is deprecated for this use).
- `olms.pipelines` — the shared item pipelines (timestamp conversion,
  header-derived file naming, employer-row cleanup, …).
- `olms.contracts.FormContract` — base for the `@*_form` contract tags.
- `olms.USER_AGENT` — the one identity both pipelines present.

Recommended settings block in a consuming scrapy project:

```python
from olms import USER_AGENT  # noqa: F401

DOWNLOADER_MIDDLEWARES = {
    "olms.middleware.BlockingBackoffMiddleware": 560,
}

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 60.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 4.0
CONCURRENT_REQUESTS_PER_DOMAIN = 4

HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 3600
HTTPCACHE_STORAGE = "olms.cache.SharedFilesystemCacheStorage"
HTTPCACHE_IGNORE_HTTP_CODES = [400, 403, 404, 429, 500, 502, 503, 504]
```

## Tests

```
pip install -e .[test]
pytest
```
