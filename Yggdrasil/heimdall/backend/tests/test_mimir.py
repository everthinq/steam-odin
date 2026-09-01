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
