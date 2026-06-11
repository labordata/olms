from unittest.mock import Mock

from olms.cache import SharedFilesystemCacheStorage


def test_cache_path_shared_across_spiders():
    storage = SharedFilesystemCacheStorage.__new__(SharedFilesystemCacheStorage)
    storage.cachedir = "/tmp/cache"
    storage._fingerprinter = Mock(
        fingerprint=Mock(return_value=bytes.fromhex("abcd" + "00" * 18))
    )
    request = Mock()

    spider_a = Mock()
    spider_a.name = "filings_incremental"
    spider_b = Mock()
    spider_b.name = "employers_incremental"

    path_a = storage._get_request_path(spider_a, request)
    path_b = storage._get_request_path(spider_b, request)

    assert path_a == path_b
    assert "/shared/" in path_a
