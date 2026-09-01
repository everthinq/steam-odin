"""Draupnir portfolio-tracker + point-in-time backup routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
import difflib
import re

from flask import Blueprint, Response, jsonify, request

from context import ctx
from validation import validate_transaction, validate_portfolio_name

bp = Blueprint('portfolios', __name__)


def _portfolio_prices(market):
    """Non-blocking {item_name: usd} price map for valuation, plus a status.

    Never waits on pulse: serves cached prices and warms in the background, so
    Draupnir pages render instantly (cost basis) and prices fill in on a later
    poll. Returns (prices_or_None, status)."""
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    return ctx.huginn_service.prices_for_valuation(token, market or 'steam')


@bp.route('/api/portfolios', methods=['GET'])
def portfolios_list():
    market = request.args.get('market', 'steam')
    prices, status = _portfolio_prices(market)
    return jsonify({
        'portfolios': ctx.portfolio_service.list_portfolios(prices),
        'priced': prices is not None,
        'pricing': status,
        'market': market,
    })


@bp.route('/api/portfolios', methods=['POST'])
def portfolios_create():
    body = request.get_json(silent=True) or {}
    errors = validate_portfolio_name(body.get('name'), required=False)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400
    return jsonify(ctx.portfolio_service.create_portfolio(body.get('name'))), 201


@bp.route('/api/portfolios/import', methods=['POST'])
def portfolios_import():
    """Import a CSV as a new portfolio (or append to ?pid=). Body: {name, csv}."""
    body = request.get_json(silent=True) or {}
    csv_text = body.get('csv')
    if not csv_text:
        return jsonify({'error': 'csv is required'}), 400
    p, count = ctx.portfolio_service.import_csv(csv_text, name=body.get('name'), pid=body.get('pid'))
    if p is None:
        return jsonify({'error': 'portfolio not found'}), 404
    return jsonify({'portfolio': {'id': p['id'], 'name': p['name']}, 'imported': count}), 201


@bp.route('/api/portfolios/combined', methods=['GET'])
def portfolios_combined():
    """Single ledger across all accounts (arbitrage flips included) — overall view."""
    market = request.args.get('market', 'steam')
    prices, status = _portfolio_prices(market)
    data = ctx.portfolio_service.combined_ledger(prices)
    data['priced'] = prices is not None
    data['pricing'] = status
    data['market'] = market
    return jsonify(data)


@bp.route('/api/portfolios/arbitrage', methods=['GET'])
def portfolios_arbitrage():
    """Count and value your tagged arbitrage deals, pooled across all accounts,
    split into steam (locked wallet) vs market (real cash) categories.
    ?market= prices any still-open tagged inventory (default steam)."""
    market = request.args.get('market', 'steam')
    prices, status = _portfolio_prices(market)
    data = ctx.portfolio_service.arbitrage_deals(prices)
    data['open_market'] = market
    data['pricing'] = status
    return jsonify(data)


@bp.route('/api/portfolios/<pid>/export', methods=['GET'])
def portfolios_export(pid):
    """Download one portfolio as a CSV (real dollars; re-importable)."""
    result = ctx.portfolio_service.export_csv(pid)
    if result is None:
        return jsonify({'error': 'portfolio not found'}), 404
    name, csv_text = result
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('_') or 'portfolio'
    return Response(
        csv_text,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{safe}.csv"'},
    )


@bp.route('/api/portfolios/validate-item', methods=['POST'])
def portfolios_validate_item():
    """Check whether a typed name looks like a real CS item. valid=True if it's in
    the pulse universe or already used in a portfolio; False (with fuzzy suggestions)
    if we have a universe to check against but it's not in it; None if we can't verify
    (no price data / no token) so the caller can allow it through."""
    name = ((request.get_json(silent=True) or {}).get('name') or '').strip()
    if not name:
        return jsonify({'valid': False, 'suggestions': []})
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    pulse_names = ctx.huginn_service.known_item_names(token)
    universe = pulse_names | ctx.portfolio_service.all_item_names()
    if name in universe:
        return jsonify({'valid': True})
    lower = {n.lower(): n for n in universe}
    if name.lower() in lower:
        return jsonify({'valid': True, 'canonical': lower[name.lower()]})
    if not pulse_names:
        return jsonify({'valid': None})   # can't verify — let the caller decide
    return jsonify({'valid': False, 'suggestions': difflib.get_close_matches(name, list(universe), n=5, cutoff=0.6)})


@bp.route('/api/portfolios/item-search', methods=['GET'])
def portfolios_item_search():
    """Typeahead for the Add-transaction form: real CS item names (pulse universe
    ∪ names already used) matching all query tokens, with the current price on the
    chosen market for prefill. Non-blocking (uses only cached pulse data)."""
    q = (request.args.get('q') or '').strip().lower()
    if len(q) < 2:
        return jsonify({'items': []})
    market = request.args.get('market', 'steam')
    token = ctx.settings_manager.get_settings().get('tradeon_token', '')
    universe = ctx.huginn_service.known_item_names(token, block=False) | ctx.portfolio_service.all_item_names()
    prices, _ = ctx.huginn_service.prices_for_valuation(token, market)
    prices = prices or {}
    tokens = q.split()
    matches = [n for n in universe if all(tok in n.lower() for tok in tokens)]
    matches.sort(key=lambda n: (not n.lower().startswith(q), len(n), n))
    return jsonify({'items': [{'name': n, 'price': prices.get(n)} for n in matches[:12]]})


@bp.route('/api/portfolios/<pid>', methods=['GET'])
def portfolios_get(pid):
    market = request.args.get('market', 'steam')
    prices, status = _portfolio_prices(market)
    data = ctx.portfolio_service.get_portfolio(pid, prices)
    if data is None:
        return jsonify({'error': 'portfolio not found'}), 404
    data['priced'] = prices is not None
    data['pricing'] = status
    data['market'] = market
    return jsonify(data)


@bp.route('/api/portfolios/<pid>', methods=['PATCH'])
def portfolios_rename(pid):
    body = request.get_json(silent=True) or {}
    errors = validate_portfolio_name(body.get('name'), required=True)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400
    p = ctx.portfolio_service.rename_portfolio(pid, body.get('name'))
    if p is None:
        return jsonify({'error': 'portfolio not found'}), 404
    return jsonify(p)


@bp.route('/api/portfolios/<pid>', methods=['DELETE'])
def portfolios_delete(pid):
    if not ctx.portfolio_service.delete_portfolio(pid):
        return jsonify({'error': 'portfolio not found'}), 404
    return jsonify({'ok': True})


@bp.route('/api/portfolios/<pid>/transactions', methods=['POST'])
def portfolios_add_txn(pid):
    body = request.get_json(silent=True) or {}
    errors = validate_transaction(body)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400
    txn = ctx.portfolio_service.add_transaction(pid, body)
    if txn is None:
        return jsonify({'error': 'portfolio not found'}), 404
    return jsonify(txn), 201


@bp.route('/api/portfolios/<pid>/transactions/<tid>', methods=['PATCH'])
def portfolios_update_txn(pid, tid):
    body = request.get_json(silent=True) or {}
    errors = validate_transaction(body, partial=True)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400
    txn = ctx.portfolio_service.update_transaction(pid, tid, body)
    if txn is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(txn)


@bp.route('/api/portfolios/<pid>/transactions/<tid>', methods=['DELETE'])
def portfolios_delete_txn(pid, tid):
    result = ctx.portfolio_service.delete_transaction(pid, tid)
    if not result:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})


# ---- Draupnir backups (point-in-time restore) -----------------------------

@bp.route('/api/portfolios/backups', methods=['GET'])
def portfolios_backups_list():
    return jsonify({
        'backups': ctx.portfolio_backup.list_backups(),
        'stats': ctx.portfolio_backup.stats(),
    })


@bp.route('/api/portfolios/backups/snapshot', methods=['POST'])
def portfolios_backups_snapshot():
    """Take a manual snapshot now (deduped if nothing changed)."""
    name = ctx.portfolio_backup.snapshot('manual')
    return jsonify({'ok': True, 'created': name, 'deduped': name is None})


@bp.route('/api/portfolios/backups/<name>/download', methods=['GET'])
def portfolios_backup_download(name):
    data = ctx.portfolio_backup.read_backup(name)
    if data is None:
        return jsonify({'error': 'backup not found'}), 404
    return Response(data, mimetype='application/json', headers={
        'Content-Disposition': f'attachment; filename="{name}"'})


@bp.route('/api/portfolios/backups/restore', methods=['POST'])
def portfolios_backup_restore():
    """Restore a snapshot over portfolios.json (current state saved first)."""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get('name')
    result = ctx.portfolio_backup.restore(name)
    if not result.get('ok'):
        code = 404 if result.get('error') == 'backup not found' else 400
        return jsonify(result), code
    ctx.portfolio_service.reload()  # pull the restored state into memory
    return jsonify({'ok': True, 'restored': name})
