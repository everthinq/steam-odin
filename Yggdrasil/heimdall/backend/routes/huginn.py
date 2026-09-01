"""Huginn (arbitrage / price-scout) routes — the largest domain: raw scans,
case alerts, the CSFloat buy-order sweep, and all the tradeon cross-market pairs.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time. The CSFloat sweep's
one-at-a-time job state lives here as module state (it was app.py-global before).
"""
import threading
import time

from flask import Blueprint, jsonify, request

from context import ctx
from huginn_service import load_csfloat_keys, load_csfloat_proxy

bp = Blueprint('huginn', __name__)

# Background CSFloat buy-order sweep state (one at a time). Progress is polled by the UI.
_csfloat_job_lock = threading.Lock()
_csfloat_job = {
    'running': False, 'done': 0, 'total': 0, 'found': 0,
    'current': None, 'started_at': None, 'finished_at': None, 'error': None,
    'waiting_until': None,   # epoch secs the sweep auto-resumes at (all keys cooling)
}


@bp.route('/api/huginn/scan', methods=['POST'])
def huginn_scan():
    """Scan all accounts and cache inventory grouped by market hash name."""
    try:
        result = ctx.huginn_service.scan()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/scan/cache', methods=['GET'])
def huginn_scan_cache():
    """Return cached inventory scan result."""
    cache = ctx.huginn_service.get_cache()
    if not cache:
        return jsonify({'error': 'No scan data yet'}), 404
    return jsonify(cache)

@bp.route('/api/huginn/cases', methods=['GET'])
def huginn_cases():
    """Case Arbitrage: compare tracked containers (cases + sticker/souvenir/autograph
    capsules) across all markets. Query: ?types=case,sticker,... (default: all)."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    raw = (request.args.get('types') or '').strip()
    categories = [t.strip() for t in raw.split(',') if t.strip()] or None
    try:
        data = ctx.huginn_service.cases_prices(token, categories)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/cases/alerts', methods=['GET'])
def huginn_cases_alerts_status():
    """Alert config + currently-active LisSkins/Buff-cheaper-than-CSFloat deals."""
    return jsonify(ctx.huginn_service.case_alert_status(ctx.settings_manager.get_settings()))

@bp.route('/api/huginn/cases/alerts/check', methods=['POST'])
def huginn_cases_alerts_check():
    """Run the alert evaluation now. ?force=1 re-sends all active deals (else only new)."""
    force = request.args.get('force') in ('1', 'true', 'yes')
    # ?refresh=1 re-pulls markets first (slower, ~20-40s) for a truly-live check; by
    # default we use the loop-maintained cache (parallel-refreshed every poll) — instant.
    refresh = request.args.get('refresh') in ('1', 'true', 'yes')
    settings = ctx.settings_manager.get_settings()
    try:
        return jsonify(ctx.huginn_service.run_case_alerts(settings, force=force, refresh=refresh))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/cases/alerts/test', methods=['POST'])
def huginn_cases_alerts_test():
    """Send a test message via the configured channel (Telegram or webhook)."""
    settings = ctx.settings_manager.get_settings()
    if notification_channel(settings) is None:
        return jsonify({'ok': False, 'error': 'No channel configured — set a Telegram bot token + chat id, or a webhook URL.'}), 400
    result = send_notification(settings, '\U0001F514 Heimdall test — Case Arbitrage alerts are wired up correctly.')
    return jsonify(result), (200 if result.get('ok') else 502)

@bp.route('/api/huginn/csfloat/buy-orders', methods=['GET'])
def huginn_csfloat_buy_orders_status():
    """Report the CSFloat buy-order sweep progress + what's currently cached."""
    with _csfloat_job_lock:
        job = dict(_csfloat_job)
    cache = ctx.huginn_service.get_csfloat_buy_orders_cache()
    key_pairs = load_csfloat_keys()
    return jsonify({
        'job': job,
        'keys': ctx.huginn_service.csfloat_keys.status(key_pairs),
        'proxy_enabled': bool(load_csfloat_proxy()),
        'cache': None if not cache else {
            'fetched_at': cache.get('fetched_at'),
            'updated_at': cache.get('updated_at'),
            'count': cache.get('count'),
            'candidates': cache.get('candidates'),
            'done': len(cache.get('processed') or []),
            'complete': cache.get('complete', True),
            'interrupted': cache.get('interrupted', False),
            'reason': cache.get('reason'),
        },
    })

@bp.route('/api/huginn/csfloat/buy-orders', methods=['POST'])
def huginn_csfloat_buy_orders_fetch():
    """Kick off a background CSFloat buy-order sweep over owned items."""
    settings = ctx.settings_manager.get_settings()
    token = settings.get('tradeon_token', '')
    if not load_csfloat_keys():
        return jsonify({'error': 'No CSFloat API keys configured — add them to csfloat_keys.json'}), 400

    scan = ctx.huginn_service.get_cache()
    if not scan or not scan.get('by_hash'):
        return jsonify({'error': 'No inventory scan yet — run "Get all items" first'}), 409

    with _csfloat_job_lock:
        if _csfloat_job['running']:
            return jsonify({'error': 'A CSFloat buy-order sweep is already running'}), 409
        _csfloat_job.update({
            'running': True, 'done': 0, 'total': 0, 'found': 0,
            'current': None, 'started_at': time.time(), 'finished_at': None, 'error': None,
            'waiting_until': None,
        })

    def _progress(done, total, current, found):
        with _csfloat_job_lock:
            _csfloat_job.update({'done': done, 'total': total, 'current': current, 'found': found})

    def _on_wait(resume_at):
        with _csfloat_job_lock:
            _csfloat_job['waiting_until'] = resume_at

    def _run():
        try:
            ctx.huginn_service.fetch_csfloat_buy_orders(token=token, progress=_progress, wait_cb=_on_wait)
        except Exception as e:
            with _csfloat_job_lock:
                _csfloat_job['error'] = str(e)
        finally:
            with _csfloat_job_lock:
                _csfloat_job['running'] = False
                _csfloat_job['finished_at'] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'started'}), 202

@bp.route('/api/huginn/tradeon/steam', methods=['GET'])
def huginn_tradeon_steam():
    """Proxy Tradeon → Steam arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_tradeon_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/buff', methods=['GET'])
def huginn_tradeon_buff():
    """Proxy Tradeon → Buff163 arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_tradeon_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/csfloat', methods=['GET'])
def huginn_tradeon_csfloat():
    """Proxy Tradeon → CSFloat arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_tradeon_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/dmarket', methods=['GET'])
def huginn_tradeon_dmarket():
    """Proxy Tradeon → DMarket (autobuy) arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_tradeon_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/lootfarm/arbitrage', methods=['GET'])
def huginn_lootfarm_arbitrage():
    """Buy LF balance cheap → acquire LF item → instant-sell into Steam/Buff/CSFloat buy
    orders. ?balance= USDT per $1 balance (default 0.5208), ?unlocked=1 (+3%), ?in_stock=1."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    try:
        balance = float(request.args.get('balance', 0.5208))
    except (TypeError, ValueError):
        balance = 0.5208
    unlocked = request.args.get('unlocked', '1') not in ('0', 'false', 'False')
    in_stock = request.args.get('in_stock', '1') not in ('0', 'false', 'False')
    try:
        return jsonify(ctx.huginn_service.lootfarm_arbitrage(token, balance, unlocked, in_stock))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/lootfarm/auctions', methods=['GET'])
def huginn_lootfarm_auctions():
    """Live LOOT.Farm auctions vs your buy sources (win price → Steam resale profit)."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    try:
        return jsonify(ctx.huginn_service.fetch_auctions(token))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/lootfarm/auctions/backtest', methods=['GET'])
def huginn_lootfarm_auctions_backtest():
    """Auction-edge stats from the tracker log + a live-snapshot proof."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    try:
        pct = float(request.args.get('min_profit', 10))
    except (TypeError, ValueError):
        pct = 10.0
    try:
        return jsonify(ctx.huginn_service.auction_backtest(token, pct))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/lootfarm/auctions/track', methods=['POST'])
def huginn_lootfarm_auctions_track():
    """Manually trigger one auction snapshot into the tracker log."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    try:
        return jsonify(ctx.huginn_service.record_auction_snapshot(token))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lootfarm', methods=['GET'])
def huginn_tradeon_lootfarm():
    """Tradeon (buy) → LOOT.Farm (sell) arbitrage. LootFarm price + limits come
    from LOOT.Farm's own feed; ?fee= is your LOOT.Farm acceptance fee % (default 5)."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        fee = float(request.args.get('fee', 5))
    except (TypeError, ValueError):
        fee = 5.0
    try:
        data = ctx.huginn_service.fetch_tradeon_lootfarm(token, fee)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lisskins-lootfarm', methods=['GET'])
def huginn_tradeon_lisskins_lootfarm():
    """LisSkins (buy) → LOOT.Farm (sell/autobuy). LootFarm price + limits from LOOT.Farm's
    own feed; ?fee= is your LOOT.Farm acceptance fee % (default 5)."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        fee = float(request.args.get('fee', 5))
    except (TypeError, ValueError):
        fee = 5.0
    try:
        data = ctx.huginn_service.fetch_lisskins_lootfarm(token, fee)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/csfloat-autobuy', methods=['GET'])
def huginn_tradeon_csfloat_autobuy():
    """Tradeon (min) buy + CSFloat buy-order (autobuy) sell, for owned items."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_tradeon_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lisskins-steam', methods=['GET'])
def huginn_tradeon_lisskins_steam():
    """Fetch LisSkins buy + Steam sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_lisskins_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lisskins-buff', methods=['GET'])
def huginn_tradeon_lisskins_buff():
    """Fetch LisSkins buy + Buff163 sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_lisskins_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lisskins-csfloat', methods=['GET'])
def huginn_tradeon_lisskins_csfloat():
    """Fetch LisSkins buy + CSFloat sell (min listing) prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_lisskins_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lisskins-csfloat-autobuy', methods=['GET'])
def huginn_tradeon_lisskins_csfloat_autobuy():
    """LisSkins (min) buy + CSFloat buy-order (autobuy) sell, for owned items."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_lisskins_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/buff-steam', methods=['GET'])
def huginn_tradeon_buff_steam():
    """Fetch Buff163 buy + Steam sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_buff_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/buff-csfloat', methods=['GET'])
def huginn_tradeon_buff_csfloat():
    """Fetch Buff163 buy + CSFloat sell (min listing) prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_buff_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/buff-csfloat-autobuy', methods=['GET'])
def huginn_tradeon_buff_csfloat_autobuy():
    """Buff163 (min) buy + CSFloat buy-order (autobuy) sell, for owned items."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_buff_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/csfloat-steam', methods=['GET'])
def huginn_tradeon_csfloat_steam():
    """Fetch CSFloat min buy + Steam autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_csfloat_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/csfloat-buff', methods=['GET'])
def huginn_tradeon_csfloat_buff():
    """Fetch CSFloat min buy + Buff163 autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_csfloat_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/lisskins-dmarket', methods=['GET'])
def huginn_tradeon_lisskins_dmarket():
    """Fetch LisSkins min buy + DMarket autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_lisskins_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/buff-dmarket', methods=['GET'])
def huginn_tradeon_buff_dmarket():
    """Fetch Buff163 min buy + DMarket autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_buff_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/csfloat-dmarket', methods=['GET'])
def huginn_tradeon_csfloat_dmarket():
    """Fetch CSFloat min buy + DMarket autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_csfloat_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/dmarket-steam', methods=['GET'])
def huginn_tradeon_dmarket_steam():
    """Fetch DMarket min buy + Steam autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_dmarket_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/dmarket-buff', methods=['GET'])
def huginn_tradeon_dmarket_buff():
    """Fetch DMarket min buy + Buff163 autobuy sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_dmarket_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/dmarket-csfloat', methods=['GET'])
def huginn_tradeon_dmarket_csfloat():
    """Fetch DMarket min buy + CSFloat min sell prices and combine into arbitrage data."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_dmarket_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/huginn/tradeon/dmarket-csfloat-autobuy', methods=['GET'])
def huginn_tradeon_dmarket_csfloat_autobuy():
    """Fetch DMarket min buy + CSFloat autobuy (buy-order) sell prices and combine."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = ctx.huginn_service.fetch_dmarket_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


