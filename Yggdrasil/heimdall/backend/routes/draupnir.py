"""Draupnir portfolio-tracker + point-in-time backup routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
import difflib
import re

from flask import Blueprint, Response, jsonify, request

from context import ctx
from huginn_service import DEFAULT_PRICE_MARKET
from draupnir_service import PortfolioSaveError, pricing_status
from validation import date_editable, normalize_datetime, validate_new_transaction, validate_portfolio_name, validate_transaction

bp = Blueprint('draupnir', __name__)


def _valuation_market():
    """The market a request values holdings on; none given means the backend's default."""
    return request.args.get('market') or DEFAULT_PRICE_MARKET


@bp.errorhandler(PortfolioSaveError)
def portfolio_save_failed(error):
    """portfolios.json could not be written: the change was not saved."""
    return jsonify({'error': str(error), 'saved': False}), 500


def _portfolio_prices(market):
    """Non-blocking {item_name: usd} price map for valuation, plus a status.

    Never waits on pulse: serves cached prices and warms in the background, so
    Draupnir pages render instantly (cost basis) and prices fill in on a later
    poll. Returns (prices_or_None, status)."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    return ctx.huginn_service.prices_for_valuation(token, market or DEFAULT_PRICE_MARKET)


def _pricing_fields(prices, status, market):
    """The live-price status every Draupnir page shows (draupnir_service.pricing_status)."""
    huginn = ctx.huginn_service
    return pricing_status(status, prices is not None,
                          refreshing_seconds=huginn.valuation_refresh_seconds(market or DEFAULT_PRICE_MARKET),
                          retry_seconds=huginn.valuation_retry_seconds())


@bp.route('/api/draupnir/portfolios', methods=['GET'])
def portfolios_list():
    market = _valuation_market()
    prices, status = _portfolio_prices(market)
    return jsonify({
        'portfolios': ctx.draupnir_service.list_portfolios(prices),
        **_pricing_fields(prices, status, market),
        'market': market,
    })


@bp.route('/api/draupnir/portfolios', methods=['POST'])
def portfolios_create():
    body = request.get_json(silent=True) or {}
    errors = validate_portfolio_name(body.get('name'), required=False)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400
    return jsonify(ctx.draupnir_service.create_portfolio(body.get('name'))), 201


@bp.route('/api/draupnir/portfolios/import/plan', methods=['POST'])
def portfolios_import_plan():
    """Check a CSV: what would be imported, which lines would be skipped and why.
    Body: {csv, name} for a new portfolio, or {csv, pid} to append. Writes nothing."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.draupnir_service.plan_import(
        body.get('csv'), name=body.get('name'), pid=body.get('pid')))


@bp.route('/api/draupnir/portfolios/import/plans/<plan_id>/execute', methods=['POST'])
def portfolios_import_execute(plan_id):
    """Import what the plan checked, once."""
    return jsonify(ctx.draupnir_service.execute_import(plan_id)), 201



@bp.route('/api/draupnir/portfolios/combined', methods=['GET'])
def portfolios_combined():
    """Single ledger across all accounts (arbitrage flips included) — overall view."""
    market = _valuation_market()
    prices, status = _portfolio_prices(market)
    data = ctx.draupnir_service.combined_ledger(prices)
    data.update(_pricing_fields(prices, status, market))
    data['market'] = market
    return jsonify(data)


@bp.route('/api/draupnir/portfolios/arbitrage', methods=['GET'])
def portfolios_arbitrage():
    """Count and value your tagged arbitrage deals, pooled across all accounts,
    split into steam (locked wallet) vs market (real cash) categories.
    ?market= prices any still-open tagged inventory (default steam)."""
    market = _valuation_market()
    prices, status = _portfolio_prices(market)
    data = ctx.draupnir_service.arbitrage_deals(prices)
    data['open_market'] = market
    data.update(_pricing_fields(prices, status, market))
    return jsonify(data)


@bp.route('/api/draupnir/portfolios/<pid>/export', methods=['GET'])
def portfolios_export(pid):
    """Download one portfolio as a CSV (real dollars; re-importable)."""
    result = ctx.draupnir_service.export_csv(pid)
    if result is None:
        return jsonify({'error': 'portfolio not found'}), 404
    name, csv_text = result
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('_') or 'portfolio'
    return Response(
        csv_text,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{safe}.csv"'},
    )


def _item_check(name):
    """The one rule for "is this a known item name": known when pulse lists it or
    a portfolio already uses it (any letter case). Answers {valid: True}, or
    {valid: False, suggestions} when pulse's list was read and the name is not
    in it, or {valid: None} when there is no list to check against (the name is
    then accepted)."""
    name = (name or '').strip()
    if not name:
        return {'valid': False, 'suggestions': []}
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    pulse_names = ctx.huginn_service.known_item_names(token)
    universe = pulse_names | ctx.draupnir_service.all_item_names()
    if name in universe:
        return {'valid': True}
    lower = {n.lower(): n for n in universe}
    if name.lower() in lower:
        return {'valid': True, 'canonical': lower[name.lower()]}
    if not pulse_names:
        return {'valid': None}
    return {'valid': False, 'suggestions': difflib.get_close_matches(name, list(universe), n=5, cutoff=0.6)}


@bp.route('/api/draupnir/portfolios/validate-item', methods=['POST'])
def portfolios_validate_item():
    """Check whether a typed name is a known item (see _item_check)."""
    return jsonify(_item_check((request.get_json(silent=True) or {}).get('name')))


def _transaction_refusal(body, errors):
    """The {error, code} answer that stops an add or edit, or None.

    ``invalid_transaction`` (400) for bad or missing fields; ``unknown_item``
    (409, with suggestions) for a name that is not in the item list, unless the
    request says ``confirm_unknown_item: true`` (a brand-new item, not indexed
    yet)."""
    if errors:
        return jsonify({'error': '; '.join(errors), 'code': 'invalid_transaction'}), 400
    name = body.get('item_name')
    if name is None or body.get('confirm_unknown_item') is True:
        return None
    check = _item_check(name)
    if check['valid'] is False:
        return jsonify({'error': f'"{name.strip()}" is not in the current item list',
                        'code': 'unknown_item', 'suggestions': check['suggestions']}), 409
    return None


@bp.route('/api/draupnir/dates/normalize', methods=['POST'])
def draupnir_dates_normalize():
    """Read a typed date or date-time the way a save reads it, so the date field can show
    the canonical form before saving: {date, editable}, or 400 {error, code: bad_date}."""
    text = (request.get_json(silent=True) or {}).get('text')
    canonical = normalize_datetime(text) if isinstance(text, str) else None
    if canonical is None:
        return jsonify({'error': 'Not a date. Type for example 2026-08-31 or 08/31/2026, 12:12 AM.',
                        'code': 'bad_date'}), 400
    return jsonify({'date': canonical, 'editable': date_editable(canonical)})


@bp.route('/api/draupnir/portfolios/item-search', methods=['GET'])
def portfolios_item_search():
    """Typeahead for the Add-transaction form: real CS item names (pulse universe
    ∪ names already used) matching all query tokens, with the current price on the
    chosen market for prefill. Non-blocking (uses only cached pulse data)."""
    q = (request.args.get('q') or '').strip().lower()
    if len(q) < 2:
        return jsonify({'items': []})
    market = _valuation_market()
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    universe = ctx.huginn_service.known_item_names(token, block=False) | ctx.draupnir_service.all_item_names()
    prices, _ = ctx.huginn_service.prices_for_valuation(token, market)
    prices = prices or {}
    tokens = q.split()
    matches = [n for n in universe if all(tok in n.lower() for tok in tokens)]
    matches.sort(key=lambda n: (not n.lower().startswith(q), len(n), n))
    return jsonify({'items': [{'name': n, 'price': prices.get(n)} for n in matches[:12]]})


@bp.route('/api/draupnir/portfolios/<pid>', methods=['GET'])
def portfolios_get(pid):
    market = _valuation_market()
    prices, status = _portfolio_prices(market)
    data = ctx.draupnir_service.get_portfolio(pid, prices)
    if data is None:
        return jsonify({'error': 'portfolio not found'}), 404
    data.update(_pricing_fields(prices, status, market))
    data['market'] = market
    return jsonify(data)


@bp.route('/api/draupnir/portfolios/<pid>', methods=['PATCH'])
def portfolios_rename(pid):
    body = request.get_json(silent=True) or {}
    errors = validate_portfolio_name(body.get('name'), required=True)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400
    p = ctx.draupnir_service.rename_portfolio(pid, body.get('name'))
    if p is None:
        return jsonify({'error': 'portfolio not found'}), 404
    return jsonify(p)


@bp.route('/api/draupnir/portfolios/<pid>', methods=['DELETE'])
def portfolios_delete(pid):
    if not ctx.draupnir_service.delete_portfolio(pid):
        return jsonify({'error': 'portfolio not found'}), 404
    return jsonify({'ok': True})


@bp.route('/api/draupnir/portfolios/<pid>/transactions', methods=['POST'])
def portfolios_add_txn(pid):
    body = request.get_json(silent=True) or {}
    refusal = _transaction_refusal(body, validate_new_transaction(body))
    if refusal:
        return refusal
    txn = ctx.draupnir_service.add_transaction(pid, body)
    if txn is None:
        return jsonify({'error': 'portfolio not found', 'code': 'portfolio_not_found'}), 404
    return jsonify(txn), 201


@bp.route('/api/draupnir/portfolios/<pid>/transactions/<tid>', methods=['PATCH'])
def portfolios_update_txn(pid, tid):
    body = request.get_json(silent=True) or {}
    refusal = _transaction_refusal(body, validate_transaction(body, partial=True))
    if refusal:
        return refusal
    txn = ctx.draupnir_service.update_transaction(pid, tid, body)
    if txn is None:
        return jsonify({'error': 'transaction not found', 'code': 'transaction_not_found'}), 404
    return jsonify(txn)


@bp.route('/api/draupnir/portfolios/<pid>/transactions/<tid>', methods=['DELETE'])
def portfolios_delete_txn(pid, tid):
    result = ctx.draupnir_service.delete_transaction(pid, tid)
    if not result:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})


# ---- Draupnir backups (point-in-time restore) -----------------------------

@bp.route('/api/draupnir/portfolios/backups', methods=['GET'])
def portfolios_backups_list():
    return jsonify({
        'backups': ctx.draupnir_backup.list_backups(),
        'stats': ctx.draupnir_backup.stats(),
    })


@bp.route('/api/draupnir/portfolios/backups/snapshot', methods=['POST'])
def portfolios_backups_snapshot():
    """Take a manual snapshot now (deduped if nothing changed)."""
    name = ctx.draupnir_backup.snapshot('manual')
    return jsonify({'ok': True, 'created': name, 'deduped': name is None})


@bp.route('/api/draupnir/portfolios/backups/<name>/download', methods=['GET'])
def portfolios_backup_download(name):
    data = ctx.draupnir_backup.read_backup(name)
    if data is None:
        return jsonify({'error': 'backup not found'}), 404
    # read_backup returns decompressed JSON, so the download is always a usable
    # .json even though snapshots are gzip-compressed on disk.
    filename = name[:-3] if name.endswith('.gz') else name
    return Response(data, mimetype='application/json', headers={
        'Content-Disposition': f'attachment; filename="{filename}"'})


@bp.route('/api/draupnir/portfolios/backups/restore', methods=['POST'])
def portfolios_backup_restore():
    """Restore a snapshot over portfolios.json (current state saved first)."""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get('name')
    # The store validates and persists the snapshot under its own lock, so a
    # write racing the restore cannot put the pre-restore state back.
    result = ctx.draupnir_backup.restore(name, store=ctx.draupnir_service)
    if not result.get('ok'):
        code = 404 if result.get('error') == 'backup not found' else 400
        return jsonify(result), code
    return jsonify({'ok': True, 'restored': name})
