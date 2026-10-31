"""Make the backend package importable from tests without installing it."""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


import pytest  # noqa: E402

import huginn_service as _huginn_service  # noqa: E402

# The real by-name lookup, for the tests that exercise it against a faked HTTP layer.
REAL_CSFLOAT_NAME_ORDERS = _huginn_service.HuginnService._csfloat_name_orders


@pytest.fixture(autouse=True)
def _no_real_csfloat_lookup_by_name(monkeypatch):
    """No test may reach CSFloat: the buy-orders-by-name lookup (a real POST) reports
    itself unsupported unless a test fakes it, so sweeps fall back to the (faked)
    listing methods the older tests patch."""
    import huginn_service

    def unsupported(self, api_key, name, **options):
        raise huginn_service._CSFloatNameLookupUnsupported('not available in tests')

    monkeypatch.setattr(huginn_service.HuginnService, '_csfloat_name_orders', unsupported)


@pytest.fixture(autouse=True)
def _no_real_pulse_request(monkeypatch):
    """No test may reach Tradeon pulse: the one transport (HuginnService._post_tradeon, which
    the master feed calls) fails loudly unless a test replaces it on its service."""
    import huginn_service

    def refused(self, url, token, body=None):
        raise AssertionError(f'a test tried to reach pulse ({url}); fake _post_tradeon on the service')

    monkeypatch.setattr(huginn_service.HuginnService, '_post_tradeon', refused)


@pytest.fixture(autouse=True)
def _price_history_in_a_temporary_folder(monkeypatch, tmp_path):
    """No test may write Heimdall's real price history (cache/price_history)."""
    import price_history
    monkeypatch.setattr(price_history, 'HISTORY_DIRECTORY', str(tmp_path / 'price_history'))


@pytest.fixture(autouse=True)
def _slaves_may_download_from_the_fake_pulse(monkeypatch):
    """In the app only Huginn Arbitrage downloads (pulse_feed's gate); the tests of each slave's own
    logic predate it and feed it tables from a fake pulse. The gate's tests switch this off."""
    import pulse_feed
    monkeypatch.setattr(pulse_feed.PulseFeed, 'slaves_may_download', True)
