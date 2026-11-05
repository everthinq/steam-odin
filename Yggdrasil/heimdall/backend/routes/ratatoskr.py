"""Ratatoskr (Storage Unit / inventory courier) routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
from flask import Blueprint, jsonify, request

from context import ctx
from storage_deposit import storage_block

bp = Blueprint('ratatoskr', __name__)


@bp.route('/api/ratatoskr/login', methods=['POST'])
def ratatoskr_login():
    """Start a Ratatoskr session for a specific account."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    data = request.json
    steam_id = data.get('steam_id')

    if not steam_id:
        return jsonify({"error": "Missing steam_id"}), 400

    # Fetch credentials from SteamService (which reads .maFiles)
    account_data = ctx.steam_service.get_account(steam_id)
    if not account_data:
        return jsonify({"error": "Account not found"}), 404

    password = data.get('password')
    if not password:
        # Try to get credentials from storage
        password = ctx.steam_service.get_password(steam_id)

    if not password:
        return jsonify({"error": "Password required (not found in storage)"}), 400

    result = ctx.ratatoskr_service.login(
        account_name=account_data.get('account_name'),
        password=password,
        shared_secret=account_data.get('shared_secret')
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/status/<steamid>', methods=['GET'])
def ratatoskr_status(steamid):
    """Check Ratatoskr session status."""
    return jsonify(ctx.ratatoskr_service.get_status(steamid))


@bp.route('/api/ratatoskr/disconnect', methods=['POST'])
def ratatoskr_disconnect():
    """End a Ratatoskr GC session."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    steam_id = request.json.get('steam_id')
    if not steam_id:
        return jsonify({"error": "Missing steam_id"}), 400

    result = ctx.ratatoskr_service.disconnect(steam_id)
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


# ---- Move commands: plan, then execute once (storage_deposit.py) ----------------
# Transfer, Auto-Store and the command line move items only through these: the rules
# (what a Storage Unit refuses, the fullest unit with room first) live in the backend.


@bp.route('/api/ratatoskr/storage/<steamid>', methods=['GET'])
def ratatoskr_storage(steamid):
    """Free places across every Storage Unit (they count as one storage)."""
    return jsonify(ctx.deposit_service.storage(steamid))


@bp.route('/api/ratatoskr/deposit/plan', methods=['POST'])
def ratatoskr_deposit_plan():
    """Plan a deposit: {steamid, item_ids: [...]} or {steamid, all_storable: true}."""
    body = request.get_json(silent=True) or {}
    if not body.get('steamid'):
        return jsonify({'error': 'Missing steamid', 'code': 'bad_request'}), 400
    item_ids = body.get('item_ids')
    if item_ids is not None and not isinstance(item_ids, list):
        return jsonify({'error': 'item_ids must be a list', 'code': 'bad_request'}), 400
    return jsonify(ctx.deposit_service.plan_deposit(
        body['steamid'], item_ids=item_ids, all_storable=bool(body.get('all_storable'))))


@bp.route('/api/ratatoskr/withdraw/plan', methods=['POST'])
def ratatoskr_withdraw_plan():
    """Plan taking items out: {steamid, items: [{item_id, casket_id}]}."""
    body = request.get_json(silent=True) or {}
    if not body.get('steamid') or not isinstance(body.get('items'), list):
        return jsonify({'error': 'Needs steamid and items', 'code': 'bad_request'}), 400
    return jsonify(ctx.deposit_service.plan_withdraw(body['steamid'], body['items']))


@bp.route('/api/ratatoskr/plans/<plan_id>/execute', methods=['POST'])
def ratatoskr_execute_plan(plan_id):
    """Run a plan once; answers its job (GET /api/jobs/<id>). Again: the same job."""
    return jsonify(ctx.deposit_service.execute(plan_id)), 202


@bp.route('/api/ratatoskr/move/status/<steamid>', methods=['GET'])
def ratatoskr_move_status(steamid):
    """Get move queue progress for an account."""
    return jsonify(ctx.ratatoskr_service.get_move_status(steamid))


@bp.route('/api/ratatoskr/config/move-delay', methods=['GET'])
def ratatoskr_get_move_delay():
    """Get delay between queued item moves (ms)."""
    result = ctx.ratatoskr_service.get_move_delay()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)


@bp.route('/api/ratatoskr/config/move-delay', methods=['POST'])
def ratatoskr_set_move_delay():
    """Set delay between queued item moves (ms)."""
    if not request.json or request.json.get('delayMs') is None:
        return jsonify({"error": "Missing delayMs"}), 400
    result = ctx.ratatoskr_service.set_move_delay(request.json.get('delayMs'))
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/config/session-idle', methods=['GET'])
def ratatoskr_get_session_idle():
    """Get Ratatoskr auto-disconnect idle timeout (ms); 0 = never."""
    result = ctx.ratatoskr_service.get_session_idle_timeout()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)


@bp.route('/api/ratatoskr/config/session-idle', methods=['POST'])
def ratatoskr_set_session_idle():
    """Set Ratatoskr auto-disconnect idle timeout (ms); 0 = never."""
    if not request.json or request.json.get('idleTimeoutMs') is None:
        return jsonify({"error": "Missing idleTimeoutMs"}), 400
    result = ctx.ratatoskr_service.set_session_idle_timeout(request.json.get('idleTimeoutMs'))
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/inventory/<steamid>', methods=['GET'])
def ratatoskr_inventory(steamid):
    """Get inventory for account; each item says whether a Storage Unit takes it
    (`storage_block`: None, or {code, reason, until}) — the backend's rule, not the page's."""
    answer = ctx.ratatoskr_service.get_inventory(steamid)
    for item in (answer or {}).get('items') or []:
        item['storage_block'] = storage_block(item)
    return jsonify(answer)


@bp.route('/api/ratatoskr/caskets/<steamid>', methods=['GET'])
def ratatoskr_caskets(steamid):
    """Get storage units for account."""
    return jsonify(ctx.ratatoskr_service.get_caskets(steamid))


@bp.route('/api/ratatoskr/casket/<steamid>/<casketid>', methods=['GET'])
def ratatoskr_casket_contents(steamid, casketid):
    """Get contents of a specific storage unit."""
    return jsonify(ctx.ratatoskr_service.get_casket_contents(steamid, casketid))


@bp.route('/api/ratatoskr/casket/rename', methods=['POST'])
def ratatoskr_casket_rename():
    """Rename a storage unit via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    result = ctx.ratatoskr_service.rename_casket(
        steam_id=request.json.get('steamID'),
        casket_id=request.json.get('casketID'),
        name=request.json.get('name', ''),
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


# ---- Rename Storage Unit (storage_rename.py) ---------------------------------------

@bp.route('/api/ratatoskr/storage-units/<steamid>', methods=['GET'])
def ratatoskr_storage_units(steamid):
    """Every Storage Unit of the account: name, items held, named or not."""
    return jsonify(ctx.rename_service.units(steamid))


@bp.route('/api/ratatoskr/storage-units/<steamid>/rename', methods=['POST'])
def ratatoskr_storage_unit_rename(steamid):
    """Rename one Storage Unit: {item_id, name}."""
    body = request.json or {}
    return jsonify(ctx.rename_service.rename(steamid, body.get('item_id'), body.get('name')))


@bp.route('/api/ratatoskr/storage-units/<steamid>/name-unnamed/plan', methods=['POST'])
def ratatoskr_storage_units_name_plan(steamid):
    """Plan: a "Storage N" name for every Storage Unit without one."""
    return jsonify(ctx.rename_service.plan_name_unnamed(steamid))


@bp.route('/api/ratatoskr/storage-units/name-plans/<plan_id>/execute', methods=['POST'])
def ratatoskr_storage_units_name_execute(plan_id):
    """Execute a naming plan, once."""
    return jsonify(ctx.rename_service.execute(plan_id))


# ---- Auto-Store (auto_store.py): one watchlist per account, one item per command ----
# Every change names one account and one item, so nothing the page did not mean can be lost.

@bp.route('/api/ratatoskr/auto-store', methods=['GET'])
def get_auto_store():
    """The switches, every account's watchlist, the master view and the move history."""
    return jsonify(ctx.auto_store.status())


@bp.route('/api/ratatoskr/auto-store/enabled', methods=['POST'])
def auto_store_enabled():
    """The master switch: {enabled: true|false}."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.auto_store.set_enabled(body.get('enabled')))


@bp.route('/api/ratatoskr/auto-store/account', methods=['POST'])
def auto_store_account():
    """Switch Auto-Store on or off for one account: {steamid, on: true|false}."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.auto_store.set_account(body.get('steamid'), body.get('on')))


@bp.route('/api/ratatoskr/auto-store/items/add', methods=['POST'])
def auto_store_add_item():
    """Add one item to one account's watchlist: {steamid, name}."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.auto_store.add_item(body.get('steamid'), body.get('name')))


@bp.route('/api/ratatoskr/auto-store/items/remove', methods=['POST'])
def auto_store_remove_item():
    """Remove one item from one account's watchlist: {steamid, name}."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.auto_store.remove_item(body.get('steamid'), body.get('name')))


@bp.route('/api/ratatoskr/auto-store/suggestions/<steamid>', methods=['GET'])
def auto_store_suggestions(steamid):
    """Names in this account's inventory a Storage Unit can hold and it does not watch yet."""
    return jsonify(ctx.auto_store.suggestions(steamid))


@bp.route('/api/ratatoskr/auto-store/sweep', methods=['POST'])
def trigger_auto_store_sweep():
    """Sweep now, one account: {steamid}. Answers {job} to follow, or {job: null, nothing}."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.scheduler.sweep_account(body.get('steamid'))), 202


# ---- Buy Storage Units, or any in-game store item (storage_shop_service.py) ----
# "item" is a price sheet entry ("casket" — the Storage Unit — when none is given).
# Commands like the moves: plan → execute once → a job; errors are {error, code}.

@bp.route('/api/ratatoskr/storage-shop', methods=['GET'])
def storage_shop_status():
    """?item=<entry>: the plan, the running job, the purchase history and statistics."""
    return jsonify(ctx.storage_shop_service.status(request.args.get('item') or 'casket'))


@bp.route('/api/ratatoskr/storage-shop/plan', methods=['POST'])
def storage_shop_plan():
    """{"item": entry, "selection": [{"steamid", "quantity"}] (none: read every wallet, no order)}:
    answers the plan in state "checking"; follow its check_job_id, then read the plan again."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.storage_shop_service.start_plan(body.get('selection'), body.get('item') or 'casket'))


@bp.route('/api/ratatoskr/storage-shop/plans/<plan_id>', methods=['GET'])
def storage_shop_plan_view(plan_id):
    """The plan: state (checking / ready / failed / replaced / expired), lines, totals, expires_at."""
    return jsonify(ctx.storage_shop_service.describe_plan(plan_id))


@bp.route('/api/ratatoskr/storage-shop/plans/<plan_id>/execute', methods=['POST'])
def storage_shop_execute(plan_id):
    """{"dry_run": bool (only false buys; missing means a dry run), "item": the entry the page
    shows (optional; must be the plan's)}: buys the plan's payable lines once and answers the
    purchase job (GET /api/jobs/<id>). Again: the same job, never a second purchase."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.storage_shop_service.execute_plan(
        plan_id, body.get('dry_run') is not False, body.get('item'))), 202


@bp.route('/api/ratatoskr/storage-shop/quote', methods=['POST'])
def storage_shop_quote():
    """{"item", "selection": [{"steamid", "quantity"}]}: the order panel's totals from what is
    last known (no Steam call, nothing planned)."""
    body = request.get_json(silent=True) or {}
    return jsonify(ctx.storage_shop_service.quote(body.get('item') or 'casket', body.get('selection')))


@bp.route('/api/ratatoskr/storage-shop/deliver-again', methods=['POST'])
def storage_shop_deliver_again():
    """{"at": the history entry's time}: ask the game store again to deliver a purchase
    left "MAY BE PAID" (it delivers only an approved transaction, so it cannot pay twice).
    Answers the delivery job."""
    body = request.get_json(silent=True) or {}
    try:
        at = float(body['at'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'at must be the purchase time', 'code': 'bad_request'}), 400
    return jsonify(ctx.storage_shop_service.start_deliver_again(at)), 202


# ---- Store Catalogue: every item the game store sells (store_catalogue_service.py) ----

@bp.route('/api/ratatoskr/store-catalogue', methods=['GET'])
def store_catalogue_status():
    """Every item in the Counter-Strike 2 in-game store with its price, and the price sheet job."""
    return jsonify(ctx.store_catalogue_service.status())


@bp.route('/api/ratatoskr/store-catalogue/refresh', methods=['POST'])
def store_catalogue_refresh():
    """Read the game store's price sheet again (one Ratatoskr login; buys nothing)."""
    result = ctx.store_catalogue_service.start_refresh()
    return jsonify(result), (200 if result['started'] else 400)
