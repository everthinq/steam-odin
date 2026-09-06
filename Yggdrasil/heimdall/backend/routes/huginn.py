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
from notifications import notification_channel, send_notification

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

@bp.route('/api/huginn/markets', methods=['GET'])
def huginn_markets():
    """Market registry for the arbitrage UI: ids, display names, autobuy support, and
    effective sell fees. Drives the generated buy->sell profile list on the frontend."""
    settings = ctx.settings_manager.get_settings()
    return jsonify(ctx.huginn_service.market_registry(settings))

@bp.route('/api/huginn/markets/fees', methods=['POST'])
def huginn_markets_fees():
    """Update the editable per-market sell fees ({"fees": {marketId: fraction}}). Only
    known market ids and fractions in [0, 1) are kept; merged into settings.json."""
    body = request.get_json(silent=True) or {}
    fees = body.get('fees')
    if not isinstance(fees, dict):
        return jsonify({'error': 'fees object required'}), 400
    known = ctx.huginn_service.market_ids()
    clean = {}
    for market_id, value in fees.items():
        if market_id not in known:
            continue
        try:
            fraction = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= fraction < 1:
            clean[market_id] = fraction
    current = dict(ctx.settings_manager.get_settings().get('huginn_market_fees') or {})
    current.update(clean)
    ctx.settings_manager.save_settings({'huginn_market_fees': current})
    return jsonify({'huginn_market_fees': current})

@bp.route('/api/huginn/tradeon/pair', methods=['GET'])
def huginn_tradeon_pair():
    """Generated arbitrage pair: ?buy=<id>&sell=<id>&mode=autobuy|min[&fee=<fraction>].
    Buys at the buy market's min listing and sells at the sell market's autobuy (or min),
    synthesised through TradeOnMarket. Same row shape as the fixed profiles."""
    settings = ctx.settings_manager.get_settings()
    token = settings.get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    buy = request.args.get('buy', '')
    sell = request.args.get('sell', '')
    mode = request.args.get('mode', 'autobuy')
    known = ctx.huginn_service.market_ids()
    if buy not in known or sell not in known:
        return jsonify({'error': 'unknown market'}), 400
    if mode not in ('autobuy', 'min'):
        mode = 'autobuy'
    fee = request.args.get('fee', type=float)
    if fee is None:
        fee = ctx.huginn_service.market_fee(sell, settings)
    try:
        data = ctx.huginn_service.fetch_generated_pair(token, buy, sell, mode, fee)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/huginn/arbitrage/cross-profile', methods=['GET'])
def huginn_cross_profile_arbitrage():
    """Best buy-min -> autobuy-sell route per item, pooled across ALL profiles.
    ?owned=1 (default: only items you hold) | ?owned=0 (whole priced universe, capped).
    ?min_pct=<float> filters out rows below that net profit %. Each row is annotated
    with which accounts hold the item and the profit across your owned units."""
    settings = ctx.settings_manager.get_settings()
    token = settings.get('tradeon_token', '')
    owned_only = request.args.get('owned', '1') not in ('0', 'false', 'False')
    min_pct = request.args.get('min_pct', type=float)
    try:
        return jsonify(ctx.cross_arbitrage_service.scan(
            token, owned_only=owned_only, min_profit_pct=min_pct, settings=settings))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/huginn/arbitrage/cross-profile/config', methods=['GET'])
def huginn_cross_profile_config_get():
    """Current cross-profile market/chain config + the full market list to toggle from:
    {buy_markets, sell_markets, chains:[{id,name,markets}], available:[{id,display,hasAutobuy}]}."""
    settings = ctx.settings_manager.get_settings()
    return jsonify(ctx.cross_arbitrage_service.config(settings))


@bp.route('/api/huginn/arbitrage/cross-profile/config', methods=['POST'])
def huginn_cross_profile_config_set():
    """Replace the cross-profile config: {buy_markets:[id], sell_markets:[id],
    chains:[{id?,name?,markets:[id,...]}]}. Unknown ids and sell markets without
    autobuy are dropped; chains need >= 2 markets. Persisted to settings.json."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.cross_arbitrage_service.save_config(body, ctx.settings_manager))

# --- Gjallarhorn (event-rotation cockpit) ------------------------------------

@bp.route('/api/huginn/gjallarhorn/rotation', methods=['GET'])
def gjallarhorn_rotation():
    """Sell list: held items scored for how well they fund a rotation into a
    freshly-limited case. ?portfolio=combined|<pid>&market=steam&steamid=<id>.
    steamid (optional) overlays a connected account's tradable-now status."""
    settings = ctx.settings_manager.get_settings()
    token = settings.get('tradeon_token', '')
    portfolio = request.args.get('portfolio', 'combined')
    market = request.args.get('market', 'steam')
    steamid = request.args.get('steamid') or None
    # Mark the page as actively in use so the liquidity warmer defers the heavy
    # authenticated history fetches to idle (keeps Steam from 429-ing the session).
    if ctx.steam_market_service is not None:
        ctx.steam_market_service.note_activity()
    try:
        return jsonify(ctx.gjallarhorn_service.rotation(token, portfolio, market, steamid))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/huginn/gjallarhorn/accounts', methods=['GET'])
def gjallarhorn_accounts():
    """Accounts available for the tradable-now overlay + readiness (SteamID64,
    name, and whether Ratatoskr currently has a live session for it)."""
    out = []
    for account in ctx.steam_service.get_all_accounts_data():
        status = ctx.ratatoskr_service.get_status(account['steamid'])
        out.append({
            'steamid': account['steamid'],
            'account_name': account['account_name'],
            'connected': status.get('status') == 'connected',
        })
    return jsonify(out)


@bp.route('/api/huginn/gjallarhorn/readiness', methods=['GET'])
def gjallarhorn_readiness():
    """Free storage-unit slots + loose-inventory count for a connected account."""
    steamid = request.args.get('steamid')
    if not steamid:
        return jsonify({'error': 'steamid required'}), 400
    try:
        return jsonify(ctx.gjallarhorn_service.readiness(steamid))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/huginn/gjallarhorn/holds', methods=['GET'])
def gjallarhorn_holds_get():
    """The instant-redeploy market whitelist (markets that don't lock balance)."""
    return jsonify(ctx.settings_manager.get_settings().get('gjallarhorn_market_holds') or [])


@bp.route('/api/huginn/gjallarhorn/holds', methods=['POST'])
def gjallarhorn_holds_set():
    """Replace the whitelist: {"holds": [{id, display, holdDays, instantRedeploy, notes}]}."""
    body = request.get_json(silent=True) or {}
    holds = body.get('holds')
    if not isinstance(holds, list):
        return jsonify({'error': 'holds array required'}), 400
    clean = ctx.gjallarhorn_service.clean_holds(holds)
    ctx.settings_manager.save_settings({'gjallarhorn_market_holds': clean})
    return jsonify(clean)


@bp.route('/api/huginn/gjallarhorn/targets', methods=['GET'])
def gjallarhorn_targets_get():
    """Priced target basket. ?capital=<usd> adds units-buyable per target."""
    settings = ctx.settings_manager.get_settings()
    token = settings.get('tradeon_token', '')
    targets = [t.get('name') for t in (settings.get('gjallarhorn_targets') or []) if t.get('name')]
    capital = request.args.get('capital', type=float)
    market = request.args.get('market', 'steam')
    try:
        return jsonify(ctx.gjallarhorn_service.basket(token, targets, capital, market))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/huginn/gjallarhorn/targets', methods=['POST'])
def gjallarhorn_targets_set():
    """Replace the target basket: {"targets": [{"name": "Kilowatt Case"}, ...]}."""
    body = request.get_json(silent=True) or {}
    targets = body.get('targets')
    if not isinstance(targets, list):
        return jsonify({'error': 'targets array required'}), 400
    clean = ctx.gjallarhorn_service.clean_targets(targets)
    ctx.settings_manager.save_settings({'gjallarhorn_targets': clean})
    return jsonify(clean)


@bp.route('/api/huginn/gjallarhorn/ring/status', methods=['GET'])
def gjallarhorn_ring_status():
    """Whether the Telegram caller (burner account) is set up, and who it rings."""
    return jsonify(ctx.telegram_caller.status())


@bp.route('/api/huginn/gjallarhorn/ring', methods=['POST'])
def gjallarhorn_ring():
    """Wake-call the target on Telegram (event alarm): send an explanatory message,
    then ring repeatedly. A bot can't call, so this rings from the configured burner
    user account via MTProto. Body (all optional): {message, ring_seconds, repeats,
    gap_seconds}. Defaults to a short test unless overridden."""
    body = request.get_json(silent=True) or {}
    message = body.get('message') or '🔔 Gjallarhorn test ring — this is what a real limiting alert will sound like.'
    kwargs = {}
    for key in ('ring_seconds', 'repeats', 'gap_seconds'):
        if body.get(key) is not None:
            try:
                kwargs[key] = int(body[key])
            except (TypeError, ValueError):
                pass
    result = ctx.telegram_caller.ring(message=message, **kwargs)
    return jsonify(result), (200 if result.get('ok') else 400)


@bp.route('/api/huginn/gjallarhorn/news/status', methods=['GET'])
def gjallarhorn_news_status():
    """Whether the CS2 news watcher is armed/running, plus recent detected events."""
    return jsonify(ctx.gjallarhorn_news_service.status())


@bp.route('/api/huginn/gjallarhorn/news/check', methods=['POST'])
def gjallarhorn_news_check():
    """Poll the CS2 update feed right now. Re-evaluates the newest post ({"force": true})
    without re-alerting the older backlog. Rings only if a new limiting event is found
    and the watcher is armed."""
    body = request.get_json(silent=True) or {}
    force = bool(body.get('force'))
    return jsonify(ctx.gjallarhorn_news_service.check_once(force=force))


@bp.route('/api/huginn/gjallarhorn/news/test', methods=['POST'])
def gjallarhorn_news_test():
    """Run the limiting-event detector on pasted post text (no alert, no ring). Lets
    Ivan verify detection against real/example update posts. Body: {"text": "..."}."""
    from gjallarhorn_news_service import detect
    body = request.get_json(silent=True) or {}
    text = body.get('text') or ''
    return jsonify({'hits': detect(text)})


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


