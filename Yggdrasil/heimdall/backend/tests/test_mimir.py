"""Tests for Mimir — the credential vault (parser + encrypted CRUD/import)."""
import pytest

from mimir_service import MimirService, parse_credentials_text
from storage import SecureStorage


def test_parser_password_with_semicolon():
    rows, warnings = parse_credentials_text(
        'hidey_spidey;BSLq=f%4%3zv;$a-;everthinklol0@gmail.com;irina@rambler.ru')
    assert not warnings
    r = rows[0]
    assert r['login'] == 'hidey_spidey'
    assert r['password'] == 'BSLq=f%4%3zv;$a-'          # ';' inside password preserved
    assert r['email'] == 'everthinklol0@gmail.com'
    assert r['comment'] == 'irina@rambler.ru'           # trailing field kept as comment


def test_parser_password_with_at_sign():
    # The password itself contains '@' — must NOT be mistaken for the email.
    rows, _ = parse_credentials_text('arthur_maddox;7@1qvy-1D6|57_km;arthur@proton.me; ban?')
    r = rows[0]
    assert r['password'] == '7@1qvy-1D6|57_km'
    assert r['email'] == 'arthur@proton.me'
    assert r['comment'] == 'ban?'


def test_parser_optional_comment_and_blank_lines():
    rows, warnings = parse_credentials_text('a_login;pw123;a@b.com\n\n  \nb_login;pw;c@d.io;note')
    assert len(rows) == 2
    assert rows[0]['comment'] == ''
    assert rows[1]['comment'] == 'note'
    assert not warnings


def test_parser_warns_on_missing_email_and_empty_password():
    rows, warnings = parse_credentials_text('lonely;justapassword')
    assert rows[0]['password'] == 'justapassword'
    assert rows[0]['email'] == ''
    assert any('no email' in w for w in warnings)


@pytest.fixture
def vault(tmp_path):
    storage = SecureStorage(storage_dir=str(tmp_path / 'maFiles'))
    return MimirService(storage), storage


def test_add_get_update_delete(vault):
    m, _ = vault
    rec = m.add(login='vincent_iles', password='pw', email='v@x.com', comment='hi')
    assert m.get_by_login('VINCENT_ILES')['password'] == 'pw'   # case-insensitive
    with pytest.raises(ValueError):
        m.add(login='vincent_iles', password='dup')             # unique login
    m.update(rec['id'], {'password': 'newpw', 'comment': ''})
    assert m.get_by_login('vincent_iles')['password'] == 'newpw'
    assert m.delete(rec['id']) is True
    assert m.get_by_login('vincent_iles') is None


def test_import_upsert_preserves_nonimported_fields(vault):
    m, _ = vault
    m.add(login='mero_sa', password='old', email='', comment='keep me')
    summary = m.import_text('mero_sa;newpw;mero@mail.com\nnew_guy;abc;g@h.com;fresh')
    assert summary['added'] == 1 and summary['updated'] == 1
    updated = m.get_by_login('mero_sa')
    assert updated['password'] == 'newpw'          # password overwritten
    assert updated['email'] == 'mero@mail.com'     # email filled in
    assert updated['comment'] == 'keep me'         # empty import field did NOT blank it


def test_record_login_result_and_get(vault):
    m, _ = vault
    rec = m.add(login='vincent_iles', password='pw')
    assert m.get(rec['id'])['last_login_status'] is None
    m.record_login_result('VINCENT_ILES', True)                 # case-insensitive
    got = m.get(rec['id'])
    assert got['last_login_status'] == 'ok' and got['last_login_at']
    m.record_login_result('vincent_iles', False, 'bad password')
    got = m.get(rec['id'])
    assert got['last_login_status'] == 'failed'
    assert got['last_login_error'] == 'bad password'
    m.record_login_result('nobody', True)                       # unknown login -> no-op, no raise


def test_export_roundtrips_through_import(vault):
    m, _ = vault
    m.import_text('hidey_spidey;BSLq=f%4%3zv;$a-;e@x.com;note\nlonely;pw;a@b.com')
    text = m.export_text()
    # A fresh vault fed the export must reproduce the same passwords/emails.
    m2 = MimirService(m.storage.__class__(storage_dir=str(m.storage.storage_dir) + '_2'))
    m2.import_text(text)
    assert m2.get_by_login('hidey_spidey')['password'] == 'BSLq=f%4%3zv;$a-'
    assert m2.get_by_login('lonely')['email'] == 'a@b.com'


def test_persistence_roundtrip_is_encrypted(tmp_path):
    d = tmp_path / 'maFiles'
    storage = SecureStorage(storage_dir=str(d))
    MimirService(storage).add(login='woodrow', password='secretpw', email='w@x.com')
    blob = (d / 'credentials.vault').read_bytes()
    assert b'secretpw' not in blob                  # at rest it is ciphertext
    # A fresh service on the same dir decrypts the same record.
    reopened = MimirService(SecureStorage(storage_dir=str(d)))
    assert reopened.get_by_login('woodrow')['password'] == 'secretpw'


# ---- export -> import round-trip (passwords with ';', '@', spaces) ----------

ROUND_TRIP_RECORDS = [
    # login, password, email, comment
    ('bob', 'pw', '', 'main acct'),                      # comment, no email
    ('semi', 'a;;b', '', ''),                            # ';;' inside, nothing after
    ('semi_note', 'a;;b', '', 'note'),                   # ';;' inside + comment
    ('trailing_semi', 'pw;', '', ''),                    # ends with ';'
    ('leading_semi', ';pw', '', 'x'),                    # starts with ';'
    ('spaces', '  pass word  ', '', ''),                 # spaces kept, not stripped
    ('spaces_email', ' pw ', 's@x.com', 'c'),
    ('at_sign', '7@1qvy-1D6|57_km', 'arthur@proton.me', 'ban?'),
    ('at_semi', 'x@y;z', '', 'c;d'),                     # '@' and ';' in password, ';' in comment
    ('email_only', 'pw', 'e@x.com', ''),
    ('bare', 'justapassword', '', ''),
]


def _fresh_vault(tmp_path, name):
    return MimirService(SecureStorage(storage_dir=str(tmp_path / name)))


def test_export_then_import_reproduces_every_record(tmp_path):
    source = _fresh_vault(tmp_path, 'source')
    for login, password, email, comment in ROUND_TRIP_RECORDS:
        source.add(login=login, password=password, email=email, comment=comment)
    target = _fresh_vault(tmp_path, 'target')
    target.import_text(source.export_text())
    for login, password, email, comment in ROUND_TRIP_RECORDS:
        got = target.get_by_login(login)
        assert (got['password'], got['email'], got['comment']) == (password, email, comment), login


def test_comment_without_email_exports_the_empty_email_slot(vault):
    m, _ = vault
    m.add(login='bob', password='pw', comment='main acct')
    assert m.export_text() == 'bob;pw;;main acct\n'
    rows, _ = parse_credentials_text('bob;pw;;main acct')
    assert rows[0] == {'login': 'bob', 'password': 'pw', 'email': '', 'comment': 'main acct'}


def test_parser_never_strips_the_password(tmp_path):
    rows, warnings = parse_credentials_text(' login ; secret ;a@b.com; note ')
    assert rows[0]['login'] == 'login'
    assert rows[0]['password'] == ' secret '
    assert rows[0]['email'] == 'a@b.com'
    assert rows[0]['comment'] == 'note'
    assert any('spaces' in w for w in warnings)


# ---- unreadable vault: refuse to save, never overwrite ----------------------

def test_unreadable_vault_refuses_every_write_and_is_left_untouched(tmp_path):
    from mimir_service import VaultUnreadableError
    directory = tmp_path / 'maFiles'
    storage = SecureStorage(storage_dir=str(directory))
    MimirService(storage).add(login='woodrow', password='secretpw')
    vault_path = directory / 'credentials.vault'
    vault_path.write_bytes(b'not a fernet token')        # simulate damage / wrong key
    damaged = vault_path.read_bytes()

    m = MimirService(SecureStorage(storage_dir=str(directory)))
    assert m.load_error is not None
    assert m.list() == []
    with pytest.raises(VaultUnreadableError):
        m.add(login='new', password='x')
    with pytest.raises(VaultUnreadableError):
        m.import_text('new;x;n@x.com')
    m.record_login_result('woodrow', True)               # annotation: no raise, no write
    assert m.list() == []                                 # refused add left memory as it was
    assert vault_path.read_bytes() == damaged             # file untouched
    assert not list(directory.glob('credentials.vault.bak-*'))


def test_unreadable_vault_route_answers_409(tmp_path, monkeypatch):
    from flask import Flask
    from context import ctx
    from routes.mimir import bp
    directory = tmp_path / 'maFiles'
    directory.mkdir()
    (directory / 'credentials.vault').write_bytes(b'garbage')
    monkeypatch.setattr(ctx, 'mimir_service',
                        MimirService(SecureStorage(storage_dir=str(directory))))
    app = Flask(__name__)
    app.register_blueprint(bp)
    response = app.test_client().post('/api/mimir/import', json={'text': 'a;b;c@d.com'})
    assert response.status_code == 409
    assert response.get_json()['vault_unreadable'] is True
    assert (directory / 'credentials.vault').read_bytes() == b'garbage'


# ---- rolling encrypted backups ----------------------------------------------

def test_each_credential_save_keeps_a_rolling_0600_backup(tmp_path):
    import stat
    import mimir_service
    directory = tmp_path / 'maFiles'
    m = MimirService(SecureStorage(storage_dir=str(directory)))
    m.add(login='first', password='one')                  # no file yet -> no backup
    assert not list(directory.glob('credentials.vault.bak-*'))
    before = (directory / 'credentials.vault').read_bytes()
    m.add(login='second', password='two')
    (backup,) = directory.glob('credentials.vault.bak-*')
    assert backup.read_bytes() == before                  # a copy of the previous ciphertext
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE((directory / 'credentials.vault').stat().st_mode) == 0o600

    for index in range(mimir_service._BACKUP_KEEP + 5):
        m.add(login=f'extra{index}', password='x')
    backups = sorted(directory.glob('credentials.vault.bak-*'))
    assert len(backups) == mimir_service._BACKUP_KEEP
    # The newest backup decrypts to the state just before the last save.
    newest = m.storage.decrypt_json(backups[-1].read_bytes())
    assert len(newest['records']) == len(m.list()) - 1


def test_login_result_stamp_does_not_rotate_backups(vault):
    m, storage = vault
    m.add(login='a', password='1')
    m.add(login='b', password='2')
    backups = sorted(storage.storage_dir.glob('credentials.vault.bak-*'))
    for _ in range(20):
        m.record_login_result('a', True)
    assert sorted(storage.storage_dir.glob('credentials.vault.bak-*')) == backups


# ---- routes: passwords are masked in bulk, served one at a time -------------

class _FakeStorage:
    """No maFiles: every credential is unlinked."""
    def list_accounts(self):
        return []


class _FakeSteamService:
    storage = _FakeStorage()


@pytest.fixture
def mimir_client(tmp_path, monkeypatch):
    from flask import Flask
    from context import ctx
    from routes.mimir import bp
    service = MimirService(SecureStorage(storage_dir=str(tmp_path / 'maFiles')))
    monkeypatch.setattr(ctx, 'mimir_service', service)
    monkeypatch.setattr(ctx, 'steam_service', _FakeSteamService())
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client(), service


def test_list_route_never_returns_passwords(mimir_client):
    client, service = mimir_client
    service.add(login='with_password', password='secret-one', email='a@x.com')
    service.add(login='without_password', password='')
    response = client.get('/api/mimir/credentials')
    assert response.status_code == 200
    assert 'secret-one' not in response.get_data(as_text=True)
    credentials = {c['login']: c for c in response.get_json()['credentials']}
    assert all('password' not in c for c in credentials.values())
    assert credentials['with_password']['has_password'] is True
    assert credentials['without_password']['has_password'] is False
    assert credentials['with_password']['email'] == 'a@x.com'   # other fields kept
    assert 'linked_steamid' in credentials['with_password']


def test_create_and_update_routes_mask_the_password(mimir_client):
    client, _ = mimir_client
    created = client.post('/api/mimir/credentials',
                          json={'login': 'new_login', 'password': 'secret-two'})
    assert created.status_code == 201
    assert 'secret-two' not in created.get_data(as_text=True)
    assert created.get_json()['has_password'] is True
    updated = client.put(f"/api/mimir/credentials/{created.get_json()['id']}",
                         json={'comment': 'note'})
    assert 'secret-two' not in updated.get_data(as_text=True)


def test_update_without_password_field_keeps_the_stored_password(mimir_client):
    client, service = mimir_client
    record = service.add(login='keep_me', password='secret-three')
    client.put(f"/api/mimir/credentials/{record['id']}", json={'email': 'k@x.com'})
    assert service.get(record['id'])['password'] == 'secret-three'


def test_password_route_returns_one_password(mimir_client):
    client, service = mimir_client
    record = service.add(login='one', password='p;@ss with spaces ')
    service.add(login='two', password='other')
    response = client.get(f"/api/mimir/credentials/{record['id']}/password")
    assert response.status_code == 200
    assert response.get_json() == {'password': 'p;@ss with spaces '}


def test_password_route_unknown_id_is_404(mimir_client):
    client, _ = mimir_client
    response = client.get('/api/mimir/credentials/does-not-exist/password')
    assert response.status_code == 404
    assert 'password' not in response.get_json()


def test_test_login_with_a_reused_session_is_not_recorded_as_ok(monkeypatch):
    """Ratatoskr reusing an open session never sends the password to Steam."""
    import routes.mimir as mimir_routes

    recorded = []
    disconnected = []

    class FakeMimir:
        def get(self, rec_id):
            return {'login': 'alpha', 'password': 'maybe-wrong'}

        def record_login_result(self, login, ok, error):
            recorded.append((login, ok, error))

    class FakeSteam:
        def get_account(self, steamid):
            return {'shared_secret': None}

    class FakeRatatoskr:
        def login(self, **kwargs):
            return {'success': True, 'steamID': '76561198000000001', 'reused': True}

        def disconnect(self, steamid):
            disconnected.append(steamid)

    from flask import Flask
    from context import ctx
    app = Flask(__name__)
    app.register_blueprint(mimir_routes.bp)
    monkeypatch.setattr(ctx, 'mimir_service', FakeMimir(), raising=False)
    monkeypatch.setattr(ctx, 'steam_service', FakeSteam(), raising=False)
    monkeypatch.setattr(ctx, 'ratatoskr_service', FakeRatatoskr(), raising=False)
    monkeypatch.setattr(mimir_routes, '_login_to_steamid', lambda: {'alpha': '76561198000000001'})

    response = app.test_client().post('/api/mimir/credentials/1/test-login')
    body = response.get_json()
    assert body['ok'] is False and body['untested'] is True
    assert recorded == [] and disconnected == []
