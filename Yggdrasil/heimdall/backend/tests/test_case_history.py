"""Price-history retention (fix #8): keep long history, compact old entries
instead of dropping them so multi-year rotation analysis survives."""
from huginn_service import HuginnService


def test_old_entries_compact_to_low_recent_keep_detail():
    rich_old = {'lo': 0.50, 'hi': 0.61, 'f': 45.0, 'mk': {'steam': [0.6, 0.62]}}
    rich_new = {'lo': 0.18, 'hi': 0.31, 'mk': {'tradeon': [0.18, 0.18]}}
    history = {
        'Revolution Case': {
            '2020-01-01': rich_old,   # older than cutoff -> compact
            '2026-08-31': rich_new,   # newer than cutoff -> keep full detail
        }
    }
    out = HuginnService._compact_history(history, full_cutoff='2026-01-01')
    series = out['Revolution Case']
    assert series['2020-01-01'] == 0.50          # collapsed to the day's low
    assert series['2026-08-31'] == rich_new      # detail preserved


def test_nothing_dropped_under_ceiling():
    # A year+ of daily points must all survive (old cap was 120 days).
    series = {f'2025-{m:02d}-{d:02d}': {'lo': 1.0}
              for m in range(1, 13) for d in (1, 15)}
    out = HuginnService._compact_history({'X': series}, full_cutoff='2000-01-01')
    assert len(out['X']) == len(series)          # 24 points, none pruned


def test_ceiling_caps_only_beyond_10_years():
    # Build more than the 3650-day ceiling; only the excess oldest is dropped.
    n = HuginnService._CASE_HISTORY_MAX_DAYS + 50
    series = {f'day-{i:05d}': i for i in range(n)}  # sortable keys
    out = HuginnService._compact_history({'X': series}, full_cutoff='0000')
    assert len(out['X']) == HuginnService._CASE_HISTORY_MAX_DAYS
    # The newest ones are kept.
    assert f'day-{n-1:05d}' in out['X']
    assert 'day-00000' not in out['X']
