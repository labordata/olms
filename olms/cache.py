from pathlib import Path

from scrapy.extensions.httpcache import FilesystemCacheStorage


class SharedFilesystemCacheStorage(FilesystemCacheStorage):
    """Cache responses in one namespace instead of per-spider.

    The stock FilesystemCacheStorage keys paths by spider.name, so
    sibling spiders (e.g. the *_incremental spiders of one update run)
    would each re-fetch the same filer detail-servlet responses.
    Sharing the namespace lets one run fetch each response once.

    Pair with HTTPCACHE_IGNORE_HTTP_CODES so error responses are not
    cached — a cached 403 would otherwise satisfy its own retry and
    poison later spiders.
    """

    def _get_request_path(self, spider, request):
        key = self._fingerprinter.fingerprint(request).hex()
        return str(Path(self.cachedir, "shared", key[0:2], key))
