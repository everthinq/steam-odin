"""Confirmations: the backend names the type and finds the key from the id."""
from steam_service import SteamService, describe_confirmation


def test_describe_prefers_steam_type_name_then_known_names():
    assert describe_confirmation({'type': 12, 'type_name': 'Purchase', 'nonce': 'n'})['type_name'] == 'Purchase'
    assert describe_confirmation({'type': 2, 'nonce': 'n'})['type_name'] == 'Trade'
    assert describe_confirmation({'type': 12, 'nonce': 'n'})['type_name'] == 'Unknown type 12'


def test_describe_picks_the_key_once():
    assert describe_confirmation({'nonce': 'n1', 'key': 'k1'})['key'] == 'n1'
    assert describe_confirmation({'key': 'k1'})['key'] == 'k1'


def _service(listed):
    svc = SteamService.__new__(SteamService)
    svc._confirmation_keys = {}
    svc.answers = []

    def get_confirmations(steamid):
        svc._confirmation_keys[str(steamid)] = {str(c['id']): c['nonce'] for c in listed}
        return {'success': True}

    def act_on_confirmation(steamid, cid, ck, operation='allow'):
        svc.answers.append((cid, ck, operation))
        return {'success': True}

    svc.get_confirmations = get_confirmations
    svc.act_on_confirmation = act_on_confirmation
    return svc


def test_respond_looks_up_the_key():
    svc = _service([{'id': 7, 'nonce': 'secret'}])
    assert svc.respond_to_confirmation('1', '7', 'allow') == {'status': 'success'}
    assert svc.answers == [('7', 'secret', 'allow')]


def test_respond_refuses_unknown_id_and_operation():
    svc = _service([])
    assert svc.respond_to_confirmation('1', '7', 'allow')['code'] == 'confirmation_not_found'
    assert svc.respond_to_confirmation('1', '7', 'approve')['code'] == 'invalid_operation'
    assert svc.answers == []
