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
