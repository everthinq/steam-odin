"""The pulse request itself: tables are asked for gzipped (~4 MB instead of ~42-59 MB of JSON)
and read whether or not the server compressed them.

Nothing here touches pulse: urlopen is replaced by a fake. The real transport is kept before
conftest's guard replaces it for every test.
"""
import gzip
import json

import pytest

import huginn_service
from huginn_service import HuginnService

REAL_POST_TRADEON = HuginnService._post_tradeon
ROWS = [{'itemName': {'marketHashName': 'AK-47 | Redline (Field-Tested)'}}]


class _Answer:
    def __init__(self, body, encoding=None):
        self._body, self.headers = body, {'Content-Encoding': encoding} if encoding else {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize('encoding', ['gzip', 'GZIP', 'x-gzip', None])
def test_a_table_is_asked_for_gzipped_and_read_either_way(monkeypatch, encoding):
    body = json.dumps(ROWS).encode('utf-8')
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        return _Answer(gzip.compress(body) if encoding else body, encoding)

    monkeypatch.setattr(huginn_service.urllib.request, 'urlopen', fake_urlopen)
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    assert REAL_POST_TRADEON(service, 'https://pulse.example/table', 'token', {}) == ROWS
    assert sent[0].get_header('Accept-encoding') == 'gzip'
