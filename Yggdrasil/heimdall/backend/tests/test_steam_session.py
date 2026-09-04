"""Tests for the Steam access-token freshness helper.

`_token_ttl_seconds` is the guard that stopped the fleet-wide confirmations
outage: a long-dead cached token must read as "needs refresh" (ttl 0) instead of
being handed to Steam and earning a `needauth`. These are pure/offline — no
network, no secrets.
"""
import base64
import json
import time

from steam_service import SteamService


def _fake_jwt(exp):
    """Build a JWT-shaped string whose payload carries the given `exp` claim.

    Only the payload segment matters to `_token_ttl_seconds`; the header and
    signature are arbitrary.
    """
    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    payload = seg({'exp': exp}) if exp is not None else seg({})
    return f"{seg({'typ': 'JWT'})}.{payload}.sig"


def test_valid_future_token_reports_positive_ttl():
    ttl = SteamService._token_ttl_seconds(_fake_jwt(int(time.time()) + 3600))
    assert 3500 < ttl <= 3600


def test_expired_token_reads_as_zero():
    assert SteamService._token_ttl_seconds(_fake_jwt(int(time.time()) - 5)) == 0


def test_missing_token_reads_as_zero():
    assert SteamService._token_ttl_seconds(None) == 0
    assert SteamService._token_ttl_seconds('') == 0


def test_unparseable_token_reads_as_zero():
    assert SteamService._token_ttl_seconds('not-a-jwt') == 0
    assert SteamService._token_ttl_seconds('a.b.c') == 0


def test_token_without_exp_reads_as_zero():
    assert SteamService._token_ttl_seconds(_fake_jwt(None)) == 0
