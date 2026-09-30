"""Mimir (credential vault) routes.

CRUD over the encrypted credential store plus a bulk text import. Every listed
credential is annotated with the steamid of a maFile whose ``account_name``
matches its login, so the UI can show which credentials are wired to a real
Steam account (and which are parked with no maFile).

Passwords never leave the backend in bulk: every record the routes return has
its password replaced by a ``has_password`` flag, and one password at a time is
served by ``GET /api/mimir/credentials/<id>/password`` (the Vault page fetches it
on demand for Reveal / Copy / Edit). The export route is the one deliberate
exception — it exists to produce the full plaintext backup.
"""
from flask import Blueprint, jsonify, request

from context import ctx
from mimir_service import VaultUnreadableError

bp = Blueprint('mimir', __name__)


@bp.errorhandler(VaultUnreadableError)
def vault_unreadable(error):
    """The vault file could not be read at boot, so every write is refused
    (the unreadable file is kept untouched). 409: the request itself is fine,
    the store is in a state that does not allow it."""
    return jsonify({'error': str(error), 'vault_unreadable': True}), 409


def _login_to_steamid():
    """Map each maFile's Steam login (account_name) -> steamid, for linkage."""
    mapping = {}
    storage = ctx.steam_service.storage
    for steamid in storage.list_accounts():
        data = storage.load_account(steamid) or {}
        name = (data.get('account_name') or '').strip().lower()
        if name:
            mapping[name] = steamid
    return mapping


def _decorate(records):
    """Annotate each record with its linked maFile steamid and mask its
    password: the password field is removed and ``has_password`` says whether
    one is stored."""
    link = _login_to_steamid()
    for record in records:
        record['linked_steamid'] = link.get(record['login'].lower())
        record['has_password'] = bool(record.pop('password', ''))
    return records


@bp.route('/api/mimir/credentials', methods=['GET'])
def list_credentials():
    return jsonify({
        'credentials': _decorate(ctx.mimir_service.list()),
        # Non-null when credentials.vault exists but could not be decrypted:
        # the list is then empty and every write is refused.
        'vault_error': ctx.mimir_service.load_error,
    })


@bp.route('/api/mimir/credentials/<rec_id>/password', methods=['GET'])
def get_credential_password(rec_id):
    """Return the password of one credential — the only route besides the
    export that sends a password to the browser."""
    record = ctx.mimir_service.get(rec_id)
    if not record:
        return jsonify({'error': 'credential not found'}), 404
    return jsonify({'password': record['password']})


@bp.route('/api/mimir/credentials', methods=['POST'])
def create_credential():
    body = request.json or {}
    try:
        rec = ctx.mimir_service.add(
            login=body.get('login', ''),
            password=body.get('password', ''),
            email=body.get('email', ''),
            comment=body.get('comment', ''),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(_decorate([rec])[0]), 201


@bp.route('/api/mimir/credentials/<rec_id>', methods=['PUT'])
def update_credential(rec_id):
    body = request.json or {}
    try:
        rec = ctx.mimir_service.update(rec_id, body)
    except KeyError:
        return jsonify({'error': 'credential not found'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(_decorate([rec])[0])


@bp.route('/api/mimir/credentials/<rec_id>', methods=['DELETE'])
def delete_credential(rec_id):
    if not ctx.mimir_service.delete(rec_id):
        return jsonify({'error': 'credential not found'}), 404
    return jsonify({'success': True})


@bp.route('/api/mimir/import', methods=['POST'])
def import_credentials():
    body = request.json or {}
    text = body.get('text', '')
    if not text.strip():
        return jsonify({'error': 'no text provided'}), 400
    summary = ctx.mimir_service.import_text(text)
    return jsonify(summary)


@bp.route('/api/mimir/credentials/<rec_id>/test-login', methods=['POST'])
def test_login(rec_id):
    """Log this one credential onto Steam via Ratatoskr and record the result.

    Needs a maFile (for the shared_secret + steamid) — a parked credential with
    no maFile cannot be tested. The outcome is stamped onto the credential so the
    table's health dot updates.
    """
    cred = ctx.mimir_service.get(rec_id)
    if not cred:
        return jsonify({'error': 'credential not found'}), 404

    steamid = _login_to_steamid().get(cred['login'].lower())
    if not steamid:
        return jsonify({'error': 'no maFile for this login — cannot test'}), 409

    account_data = ctx.steam_service.get_account(steamid) or {}
    password = cred['password'] or ctx.steam_service.get_password(steamid)
    if not password:
        ctx.mimir_service.record_login_result(cred['login'], False, 'no password')
        return jsonify({'ok': False, 'error': 'no password stored'}), 200

    result = ctx.ratatoskr_service.login(
        account_name=cred['login'],
        password=password,
        shared_secret=account_data.get('shared_secret'),
    )
    if result.get('reused'):
        # Ratatoskr already had this account logged in and reused that session, so
        # the stored password was never sent to Steam: do not report it as tested.
        return jsonify({'ok': False, 'untested': True, 'steamid': steamid,
                        'error': 'Already logged in through Ratatoskr — disconnect it first to test this password'})
    ok = 'error' not in result
    ctx.mimir_service.record_login_result(cred['login'], ok, result.get('error'))
    # A test only needs to prove the password works: log straight back out so the
    # account is not left "playing" Counter-Strike 2 (which also pauses ASF farming
    # for up to the idle timeout). A session that was already open is left alone.
    if ok:
        ctx.ratatoskr_service.disconnect(steamid)
    return jsonify({'ok': ok, 'error': result.get('error'), 'steamid': steamid})


@bp.route('/api/mimir/export', methods=['GET'])
def export_credentials():
    """Return the whole vault as importable ``login;password;email;comment`` text."""
    return jsonify({'text': ctx.mimir_service.export_text()})
